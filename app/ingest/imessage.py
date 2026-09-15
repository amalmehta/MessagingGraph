"""Real iMessage reader.

Used by `mg import`, which runs once under Full Disk Access and writes the result
into the encrypted vault; the server itself never needs access to chat.db.

What a real store needs that the synthetic corpus never did:

* **The write-ahead log.** Recent messages sit in chat.db-wal until Messages
  checkpoints. The store is snapshotted into memory with SQLite's backup API,
  which reads through the WAL and writes nothing.
* **attributedBody.** Since macOS Ventura many rows have `text` NULL and keep the
  body in an archived NSAttributedString. Skipping NULL text silently drops a
  large share of real messages.
* **Tapbacks are rows.** "Loved '...'" arrives as its own message. Counted as
  messages they would inflate volume and fill the text analysis with "Loved".
  They are folded onto their target as reactions, and removals are honoured.
* **Noise and sensitive senders.** Short codes, alphanumeric senders and business
  chats (2FA codes, bank alerts) are dropped. So are unknown numbers you have
  never written to, and messages you unsent.
* **Identifiers.** Contact, thread and message ids are keyed HMACs, so no phone
  number or email appears in an id, a URL or a log line.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..models import Contact, Corpus, Message, Thread, SELF_ID
from .base import SourceUnavailable
from ..names import group_label
from .contacts import AB_ROOT, canonical, is_machine_sender, load_people, snapshot

DB_PATH = os.path.expanduser("~/Library/Messages/chat.db")
APPLE_EPOCH_UNIX = 978307200
GROUP_STYLE = 43

TAPBACKS = {0: "love", 1: "like", 2: "dislike", 3: "laugh",
            4: "emphasize", 5: "question", 6: "emoji", 7: "sticker"}
ATTACHMENT_CHAR = "￼"
MIN_SENT_FOR_UNKNOWN = 3


def apple_to_local(value: int | float | None) -> datetime:
    """Apple timestamps are UTC since 2001; analysis reads hour-of-day, so they
    must become local wall-clock time, not naive UTC."""
    if not value:
        return datetime.fromtimestamp(APPLE_EPOCH_UNIX)
    seconds = value / 1e9 if value > 1e11 else value
    return datetime.fromtimestamp(APPLE_EPOCH_UNIX + seconds)


def local_to_apple(dt: datetime) -> int:
    return int((dt.timestamp() - APPLE_EPOCH_UNIX) * 1e9)


def decode_attributed_body(blob: bytes | None) -> str | None:
    """Pull the plain string out of an archived NSAttributedString (typedstream).

    The layout is `...NSString <class bytes> '+' <length> <utf-8 bytes>`, where a
    length byte of 0x81 means a 2-byte little-endian length follows and 0x82 a
    4-byte one. Returns None rather than guessing when the shape isn't there;
    `mg import` reports how many bodies could not be recovered, which is the real
    test of this function against your own store.
    """
    if not blob:
        return None
    for marker in (b"NSString", b"NSMutableString"):
        i = blob.find(marker)
        if i >= 0:
            break
    else:
        return None
    j = blob.find(b"+", i + len(marker))
    if j < 0 or j - i > 32:
        return None
    j += 1
    if j >= len(blob):
        return None
    n = blob[j]
    if n == 0x81:
        n, j = int.from_bytes(blob[j + 1:j + 3], "little"), j + 3
    elif n == 0x82:
        n, j = int.from_bytes(blob[j + 1:j + 5], "little"), j + 5
    else:
        j += 1
    raw = blob[j:j + n]
    if len(raw) != n:
        return None
    return raw.decode("utf-8", errors="replace")


def _display_handle(handle: str) -> str:
    digits = re.sub(r"\D", "", handle)
    if "@" not in handle and len(digits) >= 10:
        d = digits[-10:]
        return f"({d[:3]}) {d[3:6]}-{d[6:]}"
    return handle


@dataclass
class ImportReport:
    """Counts only. Nothing in here may identify a person or quote a message, so
    it is safe to print to a terminal, a log, or a chat transcript."""
    rows_read: int = 0
    kept: int = 0
    from_attributed_body: int = 0
    unrecoverable_body: int = 0
    attachment_only: int = 0
    tapbacks_folded: int = 0
    tapbacks_removed: int = 0
    dropped: Counter = field(default_factory=Counter)
    contacts_named: int = 0
    contacts_unnamed: int = 0
    handles_merged: int = 0
    threads_direct: int = 0
    threads_group: int = 0
    window_days: int = 0


class IMessageSource:
    name = "imessage"

    def __init__(self, path: str = DB_PATH, ab_root: str = AB_ROOT,
                 days: int = 400, id_key: bytes | None = None,
                 people: dict[str, tuple[str, str, bool]] | None = None) -> None:
        self.path = path
        self.ab_root = ab_root
        self.days = days
        self.id_key = id_key or secrets.token_bytes(32)
        self._people = people
        self.report = ImportReport(window_days=days)

    def _id(self, prefix: str, value: str) -> str:
        return prefix + hmac.new(self.id_key, value.encode(), hashlib.sha256).hexdigest()[:12]

    def available(self) -> tuple[bool, str]:
        if not os.path.exists(self.path):
            return False, "no chat.db at that path"
        try:
            with open(self.path, "rb") as fh:
                fh.read(16)
        except PermissionError:
            return False, ("chat.db exists but this terminal can't read it. Give the "
                           "terminal app Full Disk Access in System Settings > Privacy & "
                           "Security, reopen it, and run the import again.")
        return True, "readable"

    def load(self) -> Corpus:
        ok, why = self.available()
        if not ok:
            raise SourceUnavailable(why)
        rep = self.report
        db = snapshot(self.path)
        try:
            return self._build(db, rep)
        finally:
            db.close()

    def _build(self, db, rep: ImportReport) -> Corpus:
        mcols = {r[1] for r in db.execute("PRAGMA table_info(message)")}
        # Column names are checked bare: PRAGMA reports "attributedBody", not
        # "m.attributedBody". Getting this wrong silently defaults every optional
        # column -- no bodies recovered, tapbacks and unsent rows kept as messages.
        opt = lambda c, default="NULL": c if c.split(".")[-1] in mcols else default  # noqa: E731

        people = self._people if self._people is not None else load_people(self.ab_root)

        # Handles the user sends *from* -- their own number and Apple ID -- so that
        # a second device in a group chat doesn't show up as a friend.
        mine: set[str] = set()
        for col in ("account", "destination_caller_id"):
            if col in mcols:
                for (v,) in db.execute(f"SELECT DISTINCT {col} FROM message "
                                       f"WHERE is_from_me = 1 AND {col} IS NOT NULL"):
                    v = re.sub(r"^[EePp]:", "", str(v))
                    if v:
                        mine.add(canonical(v))

        handles = {rid: hid for rid, hid in db.execute("SELECT ROWID, id FROM handle")}
        chats = {rid: (guid, style, name) for rid, guid, style, name in
                 db.execute("SELECT ROWID, guid, style, display_name FROM chat")}
        members: dict[int, list[int]] = defaultdict(list)
        for chat_id, handle_id in db.execute("SELECT chat_id, handle_id FROM chat_handle_join"):
            members[chat_id].append(handle_id)

        cutoff = local_to_apple(datetime.now() - timedelta(days=self.days))
        rows = db.execute(f"""
            SELECT m.ROWID, m.guid, m.text, {opt('m.attributedBody')}, m.handle_id,
                   m.date, m.is_from_me, {opt('m.item_type', '0')},
                   {opt('m.associated_message_guid')}, {opt('m.associated_message_type', '0')},
                   {opt('m.date_retracted', '0')}, {opt('m.cache_has_attachments', '0')},
                   cmj.chat_id
            FROM message m JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
            WHERE m.date >= ? ORDER BY m.date, m.ROWID""", (cutoff,)).fetchall()
        rep.rows_read = len(rows)

        # Pass 1: how much has the user written into each chat? An unknown number
        # earns a place only if the user has actually talked to it.
        sent_in_chat = Counter(r[12] for r in rows if r[6] and not r[9])

        def handle_key(handle_id: int) -> str | None:
            h = handles.get(handle_id)
            if h is None or is_machine_sender(h):
                return None
            c = canonical(h)
            return None if c in mine else c

        sent_with: Counter = Counter()
        for chat_id, hs in members.items():
            for hid in hs:
                k = handle_key(hid)
                if k:
                    sent_with[k] += sent_in_chat.get(chat_id, 0)

        contacts: dict[str, Contact] = {}
        key_to_cid: dict[str, str | None] = {}
        seen_person_handles: Counter = Counter()

        def contact_for(handle_id: int) -> str | None:
            k = handle_key(handle_id)
            if k is None:
                return None
            if k in key_to_cid:
                return key_to_cid[k]
            person = people.get(k)
            if person is None and sent_with[k] < MIN_SENT_FOR_UNKNOWN:
                key_to_cid[k] = None
                return None
            identity = person[0] if person else k
            cid = self._id("p_", identity)
            if cid not in contacts:
                name = person[1] if person else _display_handle(handles[handle_id])
                # Tags record what the import knew, so the app can later ask you to
                # name someone, or recognise a business card, without the handle.
                tags = ("org",) if person and person[2] else () if person else ("unnamed",)
                contacts[cid] = Contact(id=cid, name=name, handle="", tags=tags)
                if person:
                    rep.contacts_named += 1
                else:
                    rep.contacts_unnamed += 1
            else:
                rep.handles_merged += 1
            seen_person_handles[cid] += 1
            key_to_cid[k] = cid
            return cid

        threads: dict[str, Thread] = {}
        chat_thread: dict[int, str | None] = {}
        for chat_id, (guid, style, display) in chats.items():
            cids = sorted({c for c in (contact_for(h) for h in members.get(chat_id, [])) if c})
            if not cids:
                chat_thread[chat_id] = None
                continue
            is_group = style == GROUP_STYLE or len(members.get(chat_id, [])) > 1
            if not is_group:
                tid = "t_" + cids[0][2:]
                threads.setdefault(tid, Thread(id=tid, name=contacts[cids[0]].name,
                                               participant_ids=(cids[0],), is_group=False))
            else:
                tid = self._id("g_", guid or str(chat_id))
                name = (display or "").strip() or group_label([contacts[c].name for c in cids])
                threads[tid] = Thread(id=tid, name=name,
                                      participant_ids=tuple(cids), is_group=True)
            chat_thread[chat_id] = tid

        # Pass 2: messages, with tapbacks held until their targets exist.
        by_guid: dict[str, int] = {}
        out: list[dict] = []
        pending: list[tuple[str, str, bool]] = []
        for (rid, guid, text, body, handle_id, date, from_me, item_type,
             assoc_guid, assoc_type, retracted, has_attach, chat_id) in rows:
            tid = chat_thread.get(chat_id)
            if item_type:
                rep.dropped["group events"] += 1
                continue
            if retracted:
                rep.dropped["unsent"] += 1
                continue
            if assoc_type and 2000 <= assoc_type < 4000:
                if tid is not None and assoc_guid:
                    target = assoc_guid.rsplit("/", 1)[-1].split(":")[-1]
                    kind = TAPBACKS.get(assoc_type % 1000)
                    if kind:
                        pending.append((target, kind, assoc_type >= 3000))
                continue
            if tid is None:
                rep.dropped["machine or unknown senders"] += 1
                continue
            if from_me:
                sender = SELF_ID
            else:
                sender = contact_for(handle_id)
                if sender is None:
                    rep.dropped["machine or unknown senders"] += 1
                    continue

            if text is None or not str(text).strip(ATTACHMENT_CHAR + " \n"):
                decoded = decode_attributed_body(body)
                if decoded is not None and decoded.strip(ATTACHMENT_CHAR + " \n"):
                    text = decoded
                    rep.from_attributed_body += 1
                elif body and decoded is None and not has_attach:
                    rep.unrecoverable_body += 1
            clean = (text or "").replace(ATTACHMENT_CHAR, "").strip()
            if not clean:
                if has_attach:
                    rep.attachment_only += 1
                else:
                    rep.dropped["empty"] += 1
                    continue

            by_guid[guid] = len(out)
            out.append({"id": self._id("m_", guid or str(rid)), "thread_id": tid,
                        "sender_id": sender, "ts": apple_to_local(date),
                        "text": clean, "is_from_me": bool(from_me), "reactions": []})

        for target, kind, removal in pending:
            i = by_guid.get(target)
            if i is None:
                continue
            reacts = out[i]["reactions"]
            if removal:
                if kind in reacts:
                    reacts.remove(kind)
                    rep.tapbacks_removed += 1
            else:
                reacts.append(kind)
                rep.tapbacks_folded += 1

        messages = [Message(**{**m, "reactions": tuple(m["reactions"])}) for m in out]
        messages.sort(key=lambda m: m.ts)
        live = {m.thread_id for m in messages}
        threads = {k: t for k, t in threads.items() if k in live}
        used = {c for t in threads.values() for c in t.participant_ids}
        contacts = {k: c for k, c in contacts.items() if k in used}

        rep.kept = len(messages)
        rep.threads_direct = sum(1 for t in threads.values() if not t.is_group)
        rep.threads_group = len(threads) - rep.threads_direct
        return Corpus(contacts=contacts, threads=threads, messages=messages,
                      source_name="imessage")
