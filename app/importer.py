"""Importing your messages, with no terminal in it.

The import used to run only in Terminal, so that Terminal held Full Disk Access
and the app never did. That is a sound instinct and it cost every new person a
trip through System Settings, two commands and a decision about which app to
trust -- for most people it is where the app stopped.

This is the same work, callable from the app itself. Who holds the permission
moves from Terminal to Messaging Graph, which is the app you already opened on
purpose; `access()` is what the app asks before trying, so the answer is a
sentence on screen rather than a stack trace.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DB_PATH = str(Path.home() / "Library" / "Messages" / "chat.db")
AB_ROOT = str(Path.home() / "Library" / "Application Support" / "AddressBook")

# Opens System Settings on the pane the switch is on. The app can hand this to
# the person instead of describing where to click.
SETTINGS_URL = ("x-apple.systempreferences:com.apple.preference.security"
                "?Privacy_AllFiles")


@dataclass
class Access:
    ok: bool
    reason: str = ""          # one sentence, for the screen
    missing: bool = False     # no chat.db at all, which is not a permission problem


def access(path: str = DB_PATH) -> Access:
    """Whether this process can read chat.db, asked cheaply and without side effects."""
    if not os.path.exists(path):
        return Access(False, "There's no Messages database on this Mac to read.", missing=True)
    try:
        with open(path, "rb") as fh:
            fh.read(16)
    except PermissionError:
        return Access(False, "macOS keeps your messages behind Full Disk Access, and "
                             "Messaging Graph hasn't been given it yet.")
    except OSError as e:
        return Access(False, f"chat.db couldn't be opened: {e}")
    return Access(True)


@dataclass
class Result:
    ok: bool
    messages: int = 0
    people: int = 0
    threads: int = 0
    note: str = ""


def run_import(days: int = 400, path: str = DB_PATH, ab_root: str = AB_ROOT,
               on_step=None) -> Result:
    """Read chat.db once and seal it into the vault. Returns what landed.

    `on_step` is called with a short line of progress, so a window has something
    to say during the slow part instead of appearing to hang.
    """
    def step(msg: str) -> None:
        if on_step:
            on_step(msg)

    from .ingest.imessage import IMessageSource
    from .vault import Vault, VaultError

    got = access(path)
    if not got.ok:
        return Result(False, note=got.reason)

    step("Unlocking the vault…")
    vault = Vault()
    try:
        id_key = vault.id_key()
    except VaultError as e:
        return Result(False, note=str(e))

    step(f"Reading your messages from the last {days} days…")
    src = IMessageSource(path=path, ab_root=ab_root, days=days, id_key=id_key)
    corpus = src.load()
    rep = src.report
    if rep.kept == 0:
        return Result(False, note="Nothing came back from that read, so nothing was written.")

    step("Encrypting…")
    vault.write(corpus, {"window_days": days, "messages": rep.kept,
                         "people": len(corpus.contacts), "threads": len(corpus.threads)})
    note = ""
    if rep.contacts_named == 0:
        note = "No names came from Contacts, so people will show as phone numbers."
    return Result(True, messages=rep.kept, people=len(corpus.contacts),
                  threads=len(corpus.threads), note=note)
