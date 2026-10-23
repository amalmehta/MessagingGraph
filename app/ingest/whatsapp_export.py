"""Reading the zip WhatsApp makes when you choose Export chat.

This is the way in for anyone whose WhatsApp lives on a phone: no WhatsApp
Desktop, no database, just a file per chat sent to yourself. It holds less than
the database does, and the reader is built around what is missing:

* There are no phone numbers, only the names your phone showed. Someone you
  never saved appears as their number, and only then is it keyed like one, so
  they get the same id the database reader would give them.
* Nothing says which name is yours. In a chat with one other person it is the
  name that is not theirs; across several chats it is the one in all of them;
  otherwise you are asked.
* The date format is the phone's. iPhones write "[01/06/2026, 18:00:00] ",
  Android "01/06/2026, 18:00 - ", and either may be day-first or month-first,
  24-hour or "6:00 PM". Which one is decided per file, from dates that can only
  be read one way, never guessed line by line.
* Group events -- someone joined, the subject changed, the encryption notice --
  are written as lines too. They are not things anyone said.

The zip is read in memory, the way chat.db is, and nothing is extracted: a
photo in the export is counted from its line, never opened.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import unicodedata
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from ..models import SELF_ID, Contact, Corpus, Message, Thread
from .base import SourceUnavailable

# A chat this big is not a chat; it is a zip built to fill the memory it is read into.
MAX_CHAT_BYTES = 256 * 1024 * 1024

_STAMP = (r"(\d{1,2})[./-](\d{1,2})[./-](\d{2,4}),?\s+(\d{1,2})[:.](\d{2})(?:[:.](\d{2}))?"
          r"\s*([AaPp]\.?\s?[Mm]\.?)?")
# A line may open with a direction mark, and the mark after the stamp is kept:
# on an iPhone it is how a notice says it is one.
_LEAD = r"^[‎‏﻿]*"
IOS_LINE = re.compile(_LEAD + r"\[" + _STAMP + r"\]\s(.*)$")
ANDROID_LINE = re.compile(_LEAD + _STAMP + r"\s[-–]\s(.*)$")
# The marks WhatsApp puts around names and before its own notices, and the
# narrow space iPhones put before AM/PM. All invisible, all in the way.
_INVISIBLE = dict.fromkeys(map(ord, "‎‏‪‫‬‭‮"
                                    "⁦⁧⁨⁩﻿"), None)
_SPACES = dict.fromkeys(map(ord, "   "), " ")
MEDIA = re.compile(
    r"^(?:<?(?:image|video|audio|sticker|GIF|document|contact card|Media) omitted>?"
    r"|<attached: [^>]+>|.+ \(file attached\))$", re.I)
UNSENT = re.compile(r"^(?:this message was deleted\.?|you deleted this message\.?)$", re.I)
EDITED = re.compile(r"\s*<This message was edited>\s*$", re.I)
# What WhatsApp itself writes into a chat.
NOTICE = re.compile(
    r"(?:end-to-end encrypted|changed the (?:subject|group description|group icon)|"
    r"created group|\badded\b|\bremoved\b|\bleft$|\bjoined using\b|changed (?:their|this group)|"
    r"security code|disappearing messages|is a contact|pinned a message)", re.I)
# The notices only a group has. A chat with one person can still say it is
# encrypted, or that someone is a contact.
GROUP_NOTICE = re.compile(r"(?:created group|\badded\b|\bremoved\b|\bleft$|\bjoined using\b|"
                          r"changed the (?:subject|group description|group icon))", re.I)
TITLE = re.compile(r"^WhatsApp Chat(?: with| -)\s+(.+?)(?:\.zip|\.txt)?$", re.I)
PHONE_NAME = re.compile(r"^\+?[\d\s().-]{7,}$")


@dataclass
class Report:
    rows_read: int = 0
    kept: int = 0
    attachment_only: int = 0
    threads_direct: int = 0
    threads_group: int = 0
    contacts_named: int = 0
    contacts_unnamed: int = 0
    chats: int = 0
    dropped: dict[str, int] = field(default_factory=dict)

    def drop(self, why: str, n: int = 1) -> None:
        self.dropped[why] = self.dropped.get(why, 0) + n


@dataclass
class _Line:
    when: datetime
    sender: str          # "" for a notice with nobody's name on it
    text: str
    notice: bool = False


@dataclass
class _Chat:
    title: str
    lines: list[_Line]

    @property
    def senders(self) -> set[str]:
        return {ln.sender for ln in self.lines if ln.sender and not ln.notice}


def _clean(s: str) -> str:
    s = unicodedata.normalize("NFC", s.translate(_INVISIBLE).translate(_SPACES))
    return re.sub(r"[ \t]+", " ", s).strip()


def _title(name: str) -> str:
    stem = Path(name).name
    m = TITLE.match(stem)
    return _clean(m.group(1)) if m else _clean(Path(stem).stem)


def _read_text(path: Path) -> tuple[str, str]:
    """The chat's text and its title, from a zip or a bare .txt."""
    if path.suffix.lower() == ".txt":
        return path.read_text(encoding="utf-8-sig", errors="replace"), _title(path.name)
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as e:
        raise SourceUnavailable(f"{path.name} isn't a WhatsApp export zip.") from e
    with zf:
        texts = [i for i in zf.infolist() if i.filename.lower().endswith(".txt")
                 and not i.is_dir() and "/" not in i.filename.strip("/")]
        if not texts:
            raise SourceUnavailable(f"{path.name} has no chat in it. WhatsApp puts one .txt "
                                    "file in every export.")
        chat = next((i for i in texts if i.filename == "_chat.txt"), texts[0])
        if chat.file_size > MAX_CHAT_BYTES:
            raise SourceUnavailable(f"{path.name}'s chat is {chat.file_size / 1e6:.0f} MB, "
                                    "more than any chat export holds.")
        raw = zf.read(chat)
    title = _title(path.name) if chat.filename == "_chat.txt" else _title(chat.filename)
    return raw.decode("utf-8-sig", errors="replace"), title


def _parse(text: str, title: str) -> _Chat:
    """Lines into messages, with continuation lines joined to the one before."""
    rows: list[tuple[tuple[str, ...], str]] = []
    for raw in text.splitlines():
        m = IOS_LINE.match(raw.translate(_SPACES)) or ANDROID_LINE.match(raw.translate(_SPACES))
        if m:
            rows.append((m.groups()[:7], m.group(8)))
        elif rows:
            stamp, body = rows[-1]
            rows[-1] = (stamp, body + "\n" + raw)
    # Day-first or month-first, decided by the file: one date that can only be
    # read one way settles it for every line.
    twelve_hour = any(s[6] for s, _ in rows)
    if any(int(s[0]) > 12 for s, _ in rows):
        day_first = True
    elif any(int(s[1]) > 12 for s, _ in rows):
        day_first = False
    else:
        day_first = not twelve_hour       # 6/1/26, 6:00 PM is American; 01/06/2026, 18:00 is not
    out: list[_Line] = []
    for (a, b, y, hh, mm, ss, ampm), body in rows:
        day, month = (int(a), int(b)) if day_first else (int(b), int(a))
        year = int(y) + (2000 if len(y) == 2 else 0)
        hour = int(hh)
        if ampm:
            hour = hour % 12 + (12 if ampm.lower().startswith("p") else 0)
        try:
            when = datetime(year, month, day, hour, int(mm), int(ss or 0))
        except ValueError:
            continue
        sender, sep, said = body.partition(": ")
        sender = _clean(sender)
        # iPhones write a notice as though the chat said it, with a direction
        # mark in front: "Berlin flatmates: ‎Jonas added Sofía". A photo
        # carries the same mark, from whoever sent it.
        notice = (not sep or not sender or len(sender) > 80 or '"' in sender
                  or (sender == title and said.startswith("‎")
                      and not MEDIA.match(_clean(said)) and bool(NOTICE.search(said))))
        if not sep:
            sender, said = "", body
        out.append(_Line(when, sender, said, notice=notice))
    return _Chat(title, out)


def _who_am_i(chats: list[_Chat], me: str | None) -> str:
    if me:
        me = _clean(me)
        if not any(me in c.senders for c in chats):
            raise SourceUnavailable(f"No message in these chats is from “{me}”. Use the name "
                                    "WhatsApp shows for you in the export.")
        return me
    # In a chat with one other person, you are whoever is not them.
    guesses = {next(iter(c.senders - {c.title}))
               for c in chats if c.title in c.senders and len(c.senders - {c.title}) == 1}
    if len(guesses) == 1:
        return guesses.pop()
    # Across several chats, you are the one in all of them.
    common = set.intersection(*(c.senders for c in chats)) if chats else set()
    if len(chats) > 1 and len(common) == 1:
        return common.pop()
    raise SourceUnavailable("Couldn't tell which name in these chats is yours. Run it again "
                            "with --me and the name WhatsApp shows for you.")


class WhatsAppExportSource:
    """The same shape as WhatsAppSource, read from Export chat files instead."""

    def __init__(self, paths: list[str], me: str | None = None, days: int = 400,
                 id_key: bytes | None = None) -> None:
        self.paths = [Path(p) for p in paths]
        self.me = me
        self.days = days
        self.id_key = id_key or secrets.token_bytes(32)
        self.report = Report()

    def _id(self, prefix: str, value: str) -> str:
        return prefix + hmac.new(self.id_key, value.encode(), hashlib.sha256).hexdigest()[:12]

    def _person(self, name: str) -> tuple[str, bool]:
        """(contact id, whether it is a name). A number is keyed the way the
        database keys it, so the same person gets the same id either way."""
        if PHONE_NAME.match(name):
            return self._id("w_", re.sub(r"\D", "", name) + "@s.whatsapp.net"), False
        return self._id("w_", "export/" + name.casefold()), True

    def available(self) -> tuple[bool, str]:
        if not self.paths:
            return False, "No export files given."
        missing = [p.name for p in self.paths if not p.exists()]
        if missing:
            return False, f"Not found: {', '.join(missing)}."
        return True, "readable"

    def load(self) -> Corpus:
        ok, why = self.available()
        if not ok:
            raise SourceUnavailable(why)
        rep = self.report
        chats = [_parse(*_read_text(p)) for p in self.paths]
        me = _who_am_i(chats, self.me)
        rep.chats = len(chats)
        cutoff = datetime.now() - timedelta(days=self.days)

        contacts: dict[str, Contact] = {}
        threads: dict[str, Thread] = {}
        messages: list[Message] = []
        seen: dict[str, int] = {}
        for chat in chats:
            tid = self._id("wt_", "export/" + chat.title.casefold())
            if tid in threads:          # the same chat exported twice: read it once
                rep.drop("chat exported twice", len(chat.lines))
                continue
            others = sorted(chat.senders - {me})
            is_group = len(others) != 1 or any(ln.notice and GROUP_NOTICE.search(ln.text)
                                                for ln in chat.lines)
            ids = []
            for name in others:
                cid, named = self._person(name)
                contacts.setdefault(cid, Contact(id=cid, name=name, handle="" if named else name))
                ids.append(cid)
            threads[tid] = Thread(id=tid, name=chat.title, participant_ids=tuple(ids),
                                  is_group=is_group)
            for ln in chat.lines:
                rep.rows_read += 1
                if ln.notice:
                    rep.drop("a group event, not a message")
                    continue
                if ln.when < cutoff:
                    rep.drop("outside the window")
                    continue
                text = EDITED.sub("", _clean(ln.text))
                if UNSENT.match(text):
                    rep.drop("unsent")
                    continue
                if MEDIA.match(text):
                    # A photo, a voice note: it happened, so it counts, with no text.
                    text = ""
                    rep.attachment_only += 1
                mine = ln.sender == me
                key = f"{chat.title}|{ln.when.isoformat()}|{ln.sender}"
                seen[key] = seen.get(key, 0) + 1
                messages.append(Message(
                    id=self._id("wm_", f"export/{key}|{seen[key]}"), thread_id=tid,
                    sender_id=SELF_ID if mine else self._person(ln.sender)[0],
                    ts=ln.when, text=text, is_from_me=mine))
                rep.kept += 1

        messages.sort(key=lambda m: m.ts)
        used = {m.thread_id for m in messages}
        threads = {k: t for k, t in threads.items() if k in used}
        keep = {c for t in threads.values() for c in t.participant_ids}
        contacts = {k: c for k, c in contacts.items() if k in keep}
        rep.threads_group = sum(1 for t in threads.values() if t.is_group)
        rep.threads_direct = len(threads) - rep.threads_group
        rep.contacts_unnamed = sum(1 for c in contacts.values() if c.handle)
        rep.contacts_named = len(contacts) - rep.contacts_unnamed
        return Corpus(contacts=contacts, threads=threads, messages=messages,
                      source_name="whatsapp")
