"""Reading WhatsApp's own database, the way the iMessage source reads chat.db.

WhatsApp Desktop keeps everything in a Core Data store under Group Containers.
Two things about it are worth knowing before anything else:

* It is readable without Full Disk Access. macOS protects ~/Library/Messages
  and does not protect this, so importing WhatsApp asks nothing of you at all.
* Its clock starts in 2001. Core Data counts seconds from 2001-01-01, so every
  timestamp needs 978307200 added or the whole history lands 31 years early.

A chat session is a thread, a group member is a person, and a message with no
text is a photo or a voice note -- carried as an attachment-only message, the
same way the iMessage side does it, so the counts still describe the thread.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from ..models import SELF_ID, Contact, Corpus, Message, Thread

DB_PATH = str(Path.home() / "Library" / "Group Containers" /
              "group.net.whatsapp.WhatsApp.shared" / "ChatStorage.sqlite")
CORE_DATA_EPOCH = 978307200          # 2001-01-01, where Core Data starts counting
GROUP_SESSION = 1
TEXT_MESSAGE = 0


@dataclass
class Report:
    rows_read: int = 0
    kept: int = 0
    attachment_only: int = 0
    threads_direct: int = 0
    threads_group: int = 0
    contacts_named: int = 0
    contacts_unnamed: int = 0
    dropped: dict[str, int] = field(default_factory=dict)

    def drop(self, why: str) -> None:
        self.dropped[why] = self.dropped.get(why, 0) + 1


def _when(raw: float | None) -> datetime | None:
    if raw is None:
        return None
    try:
        return datetime.fromtimestamp(raw + CORE_DATA_EPOCH)
    except (OverflowError, OSError, ValueError):
        return None


class WhatsAppSource:
    """The same shape as IMessageSource, so everything downstream is unchanged."""

    def __init__(self, path: str = DB_PATH, days: int = 400,
                 id_key: bytes | None = None) -> None:
        self.path = path
        self.days = days
        self.id_key = id_key or secrets.token_bytes(32)
        self.report = Report()

    def _id(self, prefix: str, value: str) -> str:
        return prefix + hmac.new(self.id_key, value.encode(), hashlib.sha256).hexdigest()[:12]

    def available(self) -> tuple[bool, str]:
        if not os.path.exists(self.path):
            return False, ("No WhatsApp database on this Mac. WhatsApp Desktop has to have "
                           "been opened and signed in at least once.")
        try:
            with open(self.path, "rb") as fh:
                fh.read(16)
        except PermissionError:
            return False, "WhatsApp's database is here but this app can't read it."
        except OSError as e:
            return False, f"WhatsApp's database couldn't be opened: {e}"
        return True, "readable"

    def load(self) -> Corpus:
        ok, why = self.available()
        if not ok:
            from .base import SourceUnavailable
            raise SourceUnavailable(why)
        rep = self.report
        # A copy, because WhatsApp may be running and writing to it.
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "wa.sqlite"
            shutil.copy2(self.path, copy)
            for extra in ("-wal", "-shm"):
                side = Path(self.path + extra)
                if side.exists():
                    shutil.copy2(side, str(copy) + extra)
            db = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
            db.row_factory = sqlite3.Row
            try:
                return self._read(db, rep)
            finally:
                db.close()

    def _read(self, db: sqlite3.Connection, rep: Report) -> Corpus:
        cutoff = datetime.now() - timedelta(days=self.days)
        threads: dict[str, Thread] = {}
        contacts: dict[str, Contact] = {}
        members: dict[int, list[str]] = {}

        # Who is in each group, and what WhatsApp calls them.
        for row in db.execute(
                "SELECT ZCHATSESSION, ZMEMBERJID, ZCONTACTNAME, ZFIRSTNAME FROM ZWAGROUPMEMBER"):
            jid = row["ZMEMBERJID"]
            if not jid:
                continue
            cid = self._id("w_", jid)
            name = (row["ZCONTACTNAME"] or row["ZFIRSTNAME"] or "").strip() or _from_jid(jid)
            if cid not in contacts:
                contacts[cid] = Contact(id=cid, name=name, handle=_from_jid(jid))
            members.setdefault(row["ZCHATSESSION"], []).append(cid)

        sessions: dict[int, tuple[str, bool]] = {}
        for row in db.execute("SELECT Z_PK, ZCONTACTJID, ZPARTNERNAME, ZSESSIONTYPE "
                              "FROM ZWACHATSESSION"):
            jid = row["ZCONTACTJID"] or ""
            is_group = row["ZSESSIONTYPE"] == GROUP_SESSION or jid.endswith("@g.us")
            name = (row["ZPARTNERNAME"] or "").strip() or _from_jid(jid)
            tid = self._id("wt_", jid or str(row["Z_PK"]))
            if is_group:
                people = tuple(dict.fromkeys(members.get(row["Z_PK"], [])))
            else:
                cid = self._id("w_", jid)
                # The chat's own name wins: it is the one you gave them.
                contacts[cid] = Contact(id=cid, name=name, handle=_from_jid(jid))
                people = (cid,)
            threads[tid] = Thread(id=tid, name=name, participant_ids=people, is_group=is_group)
            sessions[row["Z_PK"]] = (tid, is_group)

        messages: list[Message] = []
        for row in db.execute(
                "SELECT Z_PK, ZCHATSESSION, ZISFROMME, ZMESSAGEDATE, ZTEXT, ZMESSAGETYPE, "
                "ZGROUPMEMBER, ZFROMJID FROM ZWAMESSAGE ORDER BY ZMESSAGEDATE"):
            rep.rows_read += 1
            session = sessions.get(row["ZCHATSESSION"])
            if session is None:
                rep.drop("no chat")
                continue
            tid, is_group = session
            ts = _when(row["ZMESSAGEDATE"])
            if ts is None:
                rep.drop("no date")
                continue
            if ts < cutoff:
                rep.drop("outside the window")
                continue
            text = (row["ZTEXT"] or "").strip()
            from_me = bool(row["ZISFROMME"])
            if not text:
                # A photo, a voice note, a sticker: it happened, and leaving it
                # out would make the thread look quieter than it was.
                rep.attachment_only += 1
            sender = SELF_ID
            if not from_me:
                if is_group and row["ZGROUPMEMBER"]:
                    sender = _member_cid(db, row["ZGROUPMEMBER"], self._id) or _first(threads[tid])
                else:
                    sender = _first(threads[tid])
                if sender is None:
                    rep.drop("unknown sender")
                    continue
            messages.append(Message(id=self._id("wm_", str(row["Z_PK"])), thread_id=tid,
                                    sender_id=sender, ts=ts, text=text, is_from_me=from_me))
            rep.kept += 1

        used = {m.thread_id for m in messages}
        threads = {k: t for k, t in threads.items() if k in used}
        keep = {c for t in threads.values() for c in t.participant_ids}
        contacts = {k: c for k, c in contacts.items() if k in keep}
        rep.threads_group = sum(1 for t in threads.values() if t.is_group)
        rep.threads_direct = len(threads) - rep.threads_group
        rep.contacts_named = sum(1 for c in contacts.values() if not c.name.startswith("+"))
        rep.contacts_unnamed = len(contacts) - rep.contacts_named
        return Corpus(contacts=contacts, threads=threads, messages=messages,
                      source_name="whatsapp")


def _first(thread: Thread) -> str | None:
    return thread.participant_ids[0] if thread.participant_ids else None


def _member_cid(db: sqlite3.Connection, pk: int, mk) -> str | None:
    row = db.execute("SELECT ZMEMBERJID FROM ZWAGROUPMEMBER WHERE Z_PK = ?", (pk,)).fetchone()
    return mk("w_", row["ZMEMBERJID"]) if row and row["ZMEMBERJID"] else None


def _from_jid(jid: str) -> str:
    """A phone number out of "15551234567@s.whatsapp.net", for someone with no name."""
    digits = (jid or "").split("@")[0]
    return f"+{digits}" if digits.isdigit() else (digits or "Unknown")
