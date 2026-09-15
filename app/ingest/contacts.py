"""Resolve message handles to the names in your address book.

chat.db only knows phone numbers and email addresses. Names live in the macOS
AddressBook databases, which are covered by the same Full Disk Access grant, so
they are read once during import and never touched again.
"""
from __future__ import annotations

import glob
import os
import re
import sqlite3

AB_ROOT = os.path.expanduser("~/Library/Application Support/AddressBook")


def canonical(handle: str) -> str:
    """One key per real-world identifier, so SMS, iMessage and RCS rows for the
    same number collapse together.

    Phone numbers keep their last ten digits (so +1 (415) 555-0142 and 4155550142
    agree). That can in principle merge two international numbers sharing ten
    trailing digits; it is rare enough to accept for a personal tool.
    """
    h = handle.strip()
    if "@" in h:
        return "e:" + h.lower()
    digits = re.sub(r"\D", "", h)
    return "t:" + (digits[-10:] if len(digits) >= 10 else digits)


def is_machine_sender(handle: str) -> bool:
    """Short codes, alphanumeric senders and business chats: 2FA codes, delivery
    notices, bank alerts. Never a relationship, and often sensitive."""
    h = handle.strip()
    if h.startswith("urn:biz:"):
        return True
    if "@" in h:
        return False
    digits = re.sub(r"\D", "", h)
    if not digits:
        return True                      # "Amazon", "CHASE"
    return len(digits) <= 6              # 5-6 digit short codes


def snapshot(path: str) -> sqlite3.Connection:
    """Copy a live SQLite store into memory, WAL included.

    Opening with `immutable=1` looks tidier but ignores the write-ahead log --
    which is exactly where the most recent hours or days of messages sit until
    Messages checkpoints. The backup API reads a consistent view through the WAL
    and never writes to the source. Nothing lands on disk.
    """
    src = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        mem = sqlite3.connect(":memory:")
        src.backup(mem)
        return mem
    finally:
        src.close()


def load_people(root: str = AB_ROOT) -> dict[str, tuple[str, str, bool]]:
    """canonical handle -> (person key, display name, is a business card).

    The person key lets one address-book card with a phone number and an email
    become one contact instead of two.
    """
    people_by_handle: dict[str, tuple[str, str, bool]] = {}
    paths = [os.path.join(root, "AddressBook-v22.abcddb"),
             *glob.glob(os.path.join(root, "Sources", "*", "AddressBook-v22.abcddb"))]
    for n, path in enumerate(paths):
        if not os.path.exists(path):
            continue
        try:
            db = snapshot(path)
        except sqlite3.Error:
            continue
        try:
            people = {}
            for pk, first, last, nick, org in db.execute(
                    "SELECT Z_PK, ZFIRSTNAME, ZLASTNAME, ZNICKNAME, ZORGANIZATION "
                    "FROM ZABCDRECORD"):
                full = " ".join(x for x in (first, last) if x) or nick or org
                if full:
                    # A card with only a company name is a business, not a person.
                    people[pk] = (full.strip(), not (first or last or nick) and bool(org))
            for owner, number in db.execute(
                    "SELECT ZOWNER, ZFULLNUMBER FROM ZABCDPHONENUMBER"):
                if owner in people and number:
                    people_by_handle.setdefault(canonical(number),
                                                (f"ab{n}:{owner}", *people[owner]))
            for owner, addr in db.execute(
                    "SELECT ZOWNER, ZADDRESS FROM ZABCDEMAILADDRESS"):
                if owner in people and addr:
                    people_by_handle.setdefault(canonical(addr),
                                                (f"ab{n}:{owner}", *people[owner]))
        except sqlite3.Error:
            pass                         # schema drift in one source shouldn't sink the rest
        finally:
            db.close()
    return people_by_handle
