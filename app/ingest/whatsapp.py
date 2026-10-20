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
import re
import secrets
import sqlite3
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from ..models import SELF_ID, Contact, Corpus, Message, Thread
from .contacts import snapshot

DB_PATH = str(Path.home() / "Library" / "Group Containers" /
              "group.net.whatsapp.WhatsApp.shared" / "ChatStorage.sqlite")
CORE_DATA_EPOCH = 978307200          # 2001-01-01, where Core Data starts counting
GROUP_SESSION = 1
TEXT_MESSAGE = 0
# Status updates and the broadcast list are not conversations with anyone, and
# counting them as threads invents a relationship with nobody.
NOT_A_CONVERSATION = ("@status", "@broadcast", "@newsletter")
# WhatsApp's own account, and the entry it keeps for you.
NOT_A_PERSON = {"whatsapp", "you", "meta ai"}
# A row whose whole text is an identifier is a group event -- someone joined,
# someone left -- recorded as though it were a message.
JID_ONLY = re.compile(r"^\s*[\w.+-]+@(?:lid|s\.whatsapp\.net|g\.us|broadcast)\s*$", re.I)
GROUP_EVENT = 6
# WhatsApp sometimes stores an identifier where the name should be, and it
# arrives looking like "+CgAQvLL71QYgAw==". Nobody is called that.
NOT_A_NAME = re.compile(r"^\+?[A-Za-z0-9+/_-]{16,}={0,2}$")


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


def clean_name(raw: str | None) -> str:
    """WhatsApp wraps names in direction marks and pads numbers with
    non-breaking spaces. Both are invisible, and both travel into every
    search, every draft and every avatar unless they are taken out here."""
    if not raw:
        return ""
    out = "".join(" " if ch == "\xa0" else ch
                  for ch in raw if unicodedata.category(ch) != "Cf")
    out = re.sub(r"\s+", " ", out).strip()
    if "==" in out or (" " not in out and NOT_A_NAME.match(out)
                       and not out.lstrip("+").isdigit()):
        return ""
    return out


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
        # Into memory, the way chat.db is read. Copying the file and its WAL to
        # a temp folder left your whole history on disk in the clear -- for good,
        # if the import was killed -- and two copies taken one after the other
        # while WhatsApp is writing need not agree with each other.
        db = snapshot(self.path)
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
        member_of_row: dict[int, str] = {}      # ZWAGROUPMEMBER.Z_PK -> contact id

        # What people call themselves. Only 4 of 413 group members here have a
        # name from Contacts, and without this the rest are anonymous.
        pushed: dict[str, str] = {}
        for row in db.execute("SELECT ZJID, ZPUSHNAME FROM ZWAPROFILEPUSHNAME"):
            name = clean_name(row["ZPUSHNAME"])
            if row["ZJID"] and name:
                pushed[row["ZJID"]] = name
        # And the one carried on each message, which reaches further still.
        # Not in a group, though: there ZFROMJID is the group's own id, and the
        # push name is whichever member wrote that message.
        for row in db.execute("SELECT ZFROMJID, ZPUSHNAME FROM ZWAMESSAGE "
                              "WHERE ZPUSHNAME IS NOT NULL AND ZPUSHNAME <> '' "
                              "AND ZFROMJID IS NOT NULL AND ZFROMJID NOT LIKE '%@g.us'"):
            name = clean_name(row["ZPUSHNAME"])
            if name:
                pushed.setdefault(row["ZFROMJID"], name)

        # Who is in each group, and what WhatsApp calls them.
        for row in db.execute(
                "SELECT Z_PK, ZCHATSESSION, ZMEMBERJID, ZCONTACTNAME, ZFIRSTNAME "
                "FROM ZWAGROUPMEMBER"):
            jid = row["ZMEMBERJID"]
            if not jid:
                continue
            cid = self._id("w_", jid)
            member_of_row[row["Z_PK"]] = cid
            name = (clean_name(row["ZCONTACTNAME"]) or clean_name(row["ZFIRSTNAME"])
                    or pushed.get(jid, "") or _from_jid(jid))
            if cid not in contacts:
                contacts[cid] = Contact(id=cid, name=name, handle=_from_jid(jid))
            members.setdefault(row["ZCHATSESSION"], []).append(cid)

        sessions: dict[int, tuple[str, bool]] = {}
        for row in db.execute("SELECT Z_PK, ZCONTACTJID, ZPARTNERNAME, ZSESSIONTYPE "
                              "FROM ZWACHATSESSION"):
            jid = row["ZCONTACTJID"] or ""
            if any(jid.lower().endswith(x) for x in NOT_A_CONVERSATION):
                rep.drop("not a conversation")
                continue
            is_group = row["ZSESSIONTYPE"] == GROUP_SESSION or jid.endswith("@g.us")
            name = clean_name(row["ZPARTNERNAME"]) or pushed.get(jid, "") or _from_jid(jid)
            if not is_group and name.lower() in NOT_A_PERSON:
                rep.drop("not a person")
                continue
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
            # "179735841767521@lid" is someone joining a group, recorded as a
            # message. It is not something anyone said.
            if row["ZMESSAGETYPE"] == GROUP_EVENT or (text and JID_ONLY.match(text)):
                rep.drop("a group event, not a message")
                continue
            from_me = bool(row["ZISFROMME"])
            if not text:
                # A photo, a voice note, a sticker: it happened, and leaving it
                # out would make the thread look quieter than it was.
                rep.attachment_only += 1
            sender = SELF_ID
            if not from_me:
                if is_group:
                    # Only the member row says who wrote it. ZFROMJID is the
                    # group, and the first member listed is a guess that hands
                    # one person everybody else's messages.
                    sender = member_of_row.get(row["ZGROUPMEMBER"])
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


def _from_jid(jid: str) -> str:
    """A phone number, but only when it really is one.

    Most of this history is @lid -- WhatsApp's opaque identifier, which is not
    a phone number and is not 15 digits of one. Printing "+179735841767521"
    for it invents a number that belongs to nobody, which is worse than
    admitting there is no name.
    """
    jid = jid or ""
    local, _, domain = jid.partition("@")
    if domain.lower() == "s.whatsapp.net" and local.isdigit() and 7 <= len(local) <= 15:
        return f"+{local}"
    return "Unknown"
