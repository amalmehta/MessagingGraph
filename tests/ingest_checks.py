"""Importing chat.db as different versions of macOS have shaped it.

validate.py already reads the fixture in tests/chatdb_fixture.py as today's
macOS writes it. These read the same conversations as older releases stored them,
and as a future one might, because Apple documents none of it and changes it
without notice. Run from validate.py, which passes in its `check`.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
from datetime import timedelta


def run(check) -> None:
    from app.ingest.base import SourceUnavailable
    from app.ingest.imessage import IMessageSource, decode_attributed_body
    from tests.chatdb_fixture import ERAS, build, variant

    root = tempfile.mkdtemp(prefix="mg-eras-")
    writer, base, ab = build(os.path.join(root, "today"))
    writer.close()                    # checkpoints the WAL into chat.db, so it can be copied
    key = b"k" * 32                   # one id key, so every era names things the same way
    today = IMessageSource(path=base, ab_root=ab, days=400, id_key=key)
    ref = today.load()
    ref_texts = sorted(m.text for m in ref.messages)

    def load(era):
        path = variant(base, os.path.join(root, era), era)
        src = IMessageSource(path=path, ab_root=ab, days=400, id_key=key)
        return src, src.load()

    src, c = load("ventura-and-later")
    texts = sorted(m.text for m in c.messages)
    check("Ventura and later: extra columns change nothing that was already read",
          texts == ref_texts and len(c.contacts) == len(ref.contacts))
    target = next(m for m in c.messages if m.text == "marcus 1")
    check("Sequoia's any-emoji tapback becomes a reaction, not a message",
          target.reactions == ("emoji",) and not any("Reacted" in t for t in texts))

    src, c = load("monterey")
    texts = [m.text for m in c.messages]
    check("Monterey and earlier: no date_retracted column, and the import still works",
          len(c.messages) == len(ref.messages) + 1 and src.report.dropped.get("unsent", 0) == 0)
    check("(there was no unsending then, so a row that looks unsent is just a message)",
          "oops wrong chat" in texts)

    src, c = load("el-capitan")
    texts = [m.text for m in c.messages]
    check("before Sierra: no tapback columns, and every conversation still arrives",
          {m.thread_id for m in c.messages} == {m.thread_id for m in ref.messages})
    by_id = {m.id: m.ts for m in ref.messages}
    drift = max((abs(m.ts - by_id[m.id]) for m in c.messages if m.id in by_id),
                default=timedelta(days=1))
    check("before High Sierra: dates in seconds read as the same moments as nanoseconds",
          drift <= timedelta(seconds=1), f"largest difference {drift}")
    check("and the import window applies to seconds-era dates too",
          not any("ancient history" in t for t in texts)
          and sum(t.startswith("marcus") for t in texts) == 10)

    src, c = load("bare-minimum")
    rep = src.report
    check("only the required columns: still imports every conversation, with less detail",
          {m.thread_id for m in c.messages} == {m.thread_id for m in ref.messages}
          and rep.from_attributed_body == 0 and rep.kept > 0, f"{rep.kept} messages")

    path = variant(base, os.path.join(root, "renamed"), "renamed-handle-column")
    try:
        IMessageSource(path=path, ab_root=ab, days=400).load()
        check("a macOS that renames a column the reader needs stops with a sentence", False)
    except SourceUnavailable as e:
        check("a macOS that renames a column the reader needs stops with a sentence",
              "message.handle_id" in str(e) and "Nothing was imported" in str(e), str(e))
    except sqlite3.Error as e:
        check("a macOS that renames a column the reader needs stops with a sentence",
              False, f"raised {type(e).__name__}: {e}")
    empty = os.path.join(root, "empty.db")
    sqlite3.connect(empty).close()
    try:
        IMessageSource(path=empty, ab_root=ab, days=400).load()
        check("a file that isn't a Messages store at all is refused the same way", False)
    except SourceUnavailable as e:
        check("a file that isn't a Messages store at all is refused the same way",
              "message" in str(e))
    check("every era the docs describe is one the tests read",
          set(ERAS) == {"ventura-and-later", "monterey", "el-capitan", "bare-minimum",
                        "renamed-handle-column"})
    from pathlib import Path
    doc = Path("docs/SYSTEM-DESIGN.md").read_text()
    check("and the schema notes in SYSTEM-DESIGN.md name each of them",
          all(e in doc for e in ERAS), ", ".join(e for e in ERAS if e not in doc))
    from app import importer
    check("the importer turns a schema it can't read into a sentence on screen",
          "except SourceUnavailable" in Path(importer.__file__).read_text())

    # attributedBody comes in more shapes than the fixture's usual one.
    def stream(cls: bytes, length: bytes, body: bytes) -> bytes:
        return (b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84\x12NSAttributedString"
                b"\x00\x84\x84\x08NSObject\x00\x85\x92\x84\x84\x84" + bytes([len(cls)]) + cls
                + b"\x01\x94\x84\x01+" + length + body + b"\x86\x84")
    big = ("long " * 14000).encode()                       # over 65,535 bytes
    check("attributedBody with a four-byte length decodes",
          decode_attributed_body(stream(b"NSString", b"\x82" + len(big).to_bytes(4, "little"),
                                        big)) == big.decode())
    check("attributedBody archived as NSMutableString decodes",
          decode_attributed_body(stream(b"NSMutableString", b"\x05", b"hello")) == "hello")
    check("a truncated or unfamiliar attributedBody returns nothing rather than a guess",
          decode_attributed_body(stream(b"NSString", b"\x40", b"short")) is None
          and decode_attributed_body(b"\x04\x0bstreamtyped garbage") is None
          and decode_attributed_body(b"") is None)
