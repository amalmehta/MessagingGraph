"""A small chat.db and AddressBook shaped like the real ones.

Built to exercise what synthetic data never did: rows that exist only in the
write-ahead log, bodies stored only in attributedBody, tapbacks and their
removal, duplicate SMS/iMessage handles, one person reachable by phone and email,
short-code 2FA senders, an unknown number never replied to, unsent messages,
attachment-only messages, the user's own Apple ID inside a group -- and names and
message text written by someone hostile, because anyone can text you and anyone
in a group can rename it.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta

from app.ingest.imessage import local_to_apple

XSS_GROUP = '<img src=x onerror="document.title=\'pwned-group\'">'
XSS_MSG = "<script>document.title='pwned-msg'</script>"
XSS_MSG2 = '<img src=x onerror="document.title=\'pwned-msg2\'">'
XSS_NAME_LAST = '<img src=x onerror="document.title=\'pwned-name\'">'
LONG_BODY = ("okay so this is a long one on purpose — it has to cross 127 bytes so the "
             "length prefix switches to the two-byte form 🎉🎉 and still decodes cleanly")
WAL_MARKER = "wal-only marker"


def attributed(s: str) -> bytes:
    b = s.encode()
    ln = bytes([len(b)]) if len(b) < 0x80 else b"\x81" + len(b).to_bytes(2, "little")
    return (b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84\x12NSAttributedString"
            b"\x00\x84\x84\x08NSObject\x00\x85\x92\x84\x84\x84\x08NSString\x01\x94\x84\x01+"
            + ln + b + b"\x86\x84\x02iI\x01\x05\x92\x84\x84\x84\x0cNSDictionary\x00\x94")


def _addressbook(root: str) -> None:
    os.makedirs(root, exist_ok=True)
    path = os.path.join(root, "AddressBook-v22.abcddb")
    if os.path.exists(path):
        os.remove(path)
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE ZABCDRECORD (Z_PK INTEGER PRIMARY KEY, ZFIRSTNAME TEXT,
            ZLASTNAME TEXT, ZNICKNAME TEXT, ZORGANIZATION TEXT);
        CREATE TABLE ZABCDPHONENUMBER (ZOWNER INTEGER, ZFULLNUMBER TEXT);
        CREATE TABLE ZABCDEMAILADDRESS (ZOWNER INTEGER, ZADDRESS TEXT);
    """)
    db.executemany("INSERT INTO ZABCDRECORD VALUES (?,?,?,?,?)", [
        (1, "Priya", "Raman", None, None),
        (2, "Marcus", "Webb", None, None),
        (3, "Dana", XSS_NAME_LAST, None, None),
        (4, None, None, None, "Sunrise Dental"),
    ])
    db.executemany("INSERT INTO ZABCDPHONENUMBER VALUES (?,?)", [
        (1, "(415) 555-0142"), (2, "+1 415-555-0188"), (3, "415.555.0166"), (4, "(415) 555-0133")])
    db.execute("INSERT INTO ZABCDEMAILADDRESS VALUES (1, 'Priya@Example.com')")
    db.commit()
    db.close()


def build(root: str):
    """Write the fixture under `root`. Returns (open_writer, chat_db_path, ab_root).

    The writer is left open with autocheckpoint disabled so the newest rows stay
    in chat.db-wal -- close it only after the reader has run.
    """
    os.makedirs(root, exist_ok=True)
    ab_root = os.path.join(root, "AddressBook")
    _addressbook(ab_root)
    path = os.path.join(root, "chat.db")
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(path + suffix):
            os.remove(path + suffix)

    db = sqlite3.connect(path)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA wal_autocheckpoint=0")
    db.executescript("""
        CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT, service TEXT);
        CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, guid TEXT, style INTEGER,
            display_name TEXT, chat_identifier TEXT);
        CREATE TABLE chat_handle_join (chat_id INTEGER, handle_id INTEGER);
        CREATE TABLE message (ROWID INTEGER PRIMARY KEY, guid TEXT, text TEXT,
            attributedBody BLOB, handle_id INTEGER, date INTEGER, is_from_me INTEGER,
            item_type INTEGER DEFAULT 0, associated_message_guid TEXT,
            associated_message_type INTEGER DEFAULT 0, date_retracted INTEGER DEFAULT 0,
            cache_has_attachments INTEGER DEFAULT 0, account TEXT,
            destination_caller_id TEXT);
        CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER,
            message_date INTEGER);
    """)
    db.executemany("INSERT INTO handle VALUES (?,?,?)", [
        (1, "+14155550142", "iMessage"), (2, "+14155550142", "SMS"),
        (3, "priya@example.com", "iMessage"), (4, "+14155550188", "iMessage"),
        (5, "+14155550199", "SMS"), (6, "+14155550177", "SMS"),
        (7, "22395", "SMS"), (8, "me@icloud.com", "iMessage"),
        (9, "+14155550166", "iMessage"), (10, "+14155550133", "SMS"),
        (11, "+14155550144", "SMS"), (12, "+14085550111", "SMS"), (13, "+14085550222", "SMS"),
    ])
    db.executemany("INSERT INTO chat VALUES (?,?,?,?,?)", [
        (1, "iMessage;-;+14155550142", 45, None, "+14155550142"),
        (2, "SMS;-;+14155550142", 45, None, "+14155550142"),
        (3, "iMessage;-;priya@example.com", 45, None, "priya@example.com"),
        (4, "iMessage;-;+14155550188", 45, None, "+14155550188"),
        (5, "SMS;-;+14155550199", 45, None, "+14155550199"),
        (6, "SMS;-;+14155550177", 45, None, "+14155550177"),
        (7, "SMS;-;22395", 45, None, "22395"),
        (8, "iMessage;+;chat123", 43, XSS_GROUP, "chat123"),
        (9, "iMessage;-;+14155550166", 45, None, "+14155550166"),
        (10, "SMS;-;+14155550133", 45, None, "+14155550133"),
        (11, "SMS;-;+14155550144", 45, None, "+14155550144"),
        (12, "SMS;+;chat408", 43, None, "chat408"),
    ])
    db.executemany("INSERT INTO chat_handle_join VALUES (?,?)", [
        (1, 1), (2, 2), (3, 3), (4, 4), (5, 5), (6, 6), (7, 7),
        (8, 1), (8, 4), (8, 8), (9, 9), (10, 10), (11, 11), (12, 12), (12, 13)])

    now = datetime.now()
    n = [0]

    def msg(chat, handle, days_ago, text=None, *, mine=False, body=None, guid=None,
            item_type=0, assoc=None, assoc_type=0, retracted=0, attach=0, minutes=0):
        n[0] += 1
        rid = n[0]
        date = local_to_apple(now - timedelta(days=days_ago, minutes=minutes))
        db.execute("INSERT INTO message VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            rid, guid or f"G{rid}", text, body, 0 if mine else handle, date, int(mine),
            item_type, assoc, assoc_type, retracted, attach,
            "E:me@icloud.com" if mine else None, "me@icloud.com" if mine else None))
        db.execute("INSERT INTO chat_message_join VALUES (?,?,?)", (chat, rid, date))

    for i in range(30):                       # Priya over iMessage
        d = 50 - i * 1.6
        if i % 3 == 0:
            msg(1, 1, d, None, body=attributed(f"body-only message {i} from priya"))
        elif i == 7:
            msg(1, 1, d, None, body=attributed(LONG_BODY))
        elif i == 10:
            msg(1, 1, d, "can't wait to see you!", guid="P-LOVE-TARGET")
        else:
            msg(1, 1, d, f"priya says {i}", mine=(i % 2 == 1))
    msg(1, 1, 33, mine=True, assoc="p:0/P-LOVE-TARGET", assoc_type=2000, text="Loved “…”")
    msg(1, 1, 33, mine=True, assoc="p:0/P-LOVE-TARGET", assoc_type=2003, minutes=-1,
        text="Laughed at “…”")
    msg(1, 1, 33, mine=True, assoc="p:0/P-LOVE-TARGET", assoc_type=3003, minutes=-2,
        text="Removed a laugh from “…”")
    for i in range(4):
        msg(2, 2, 20 - i, f"sms to priya {i}", mine=(i % 2 == 0))
    for i in range(3):
        msg(3, 3, 12 - i, f"email-handle priya {i}", mine=(i == 1))

    for i in range(10):                       # Marcus
        msg(4, 4, 40 - i * 3, f"marcus {i}", mine=(i % 2 == 0))
    msg(4, 4, 9, "oops wrong chat", mine=True, retracted=1)
    msg(4, 4, 8, "￼", attach=1)
    msg(4, 4, 500, "ancient history, outside the window")

    for i in range(5):
        msg(5, 5, 25 - i, f"to unknown {i}", mine=True)
    for i in range(3):
        msg(5, 5, 24 - i, f"unknown replies {i}")
    for i in range(4):
        msg(6, 6, 30 - i, "WIN A FREE CRUISE reply YES")
    for i in range(3):
        msg(7, 7, 5 - i, f"Your verification code is 48291{i}")

    for i in range(12):                       # group, named by someone hostile
        if i == 3:
            msg(8, 1, 15, XSS_MSG)
        elif i == 5:
            msg(8, 4, 14, XSS_MSG2)
        else:
            msg(8, 1 if i % 3 else 4, 18 - i, f"group chatter {i}", mine=(i % 4 == 0))
    msg(8, 1, 16, None, item_type=1)
    for i in range(6):
        msg(9, 9, 22 - i, f"dana {i}", mine=(i % 2 == 0))
    for i, when in enumerate(["Tue 9/2 at 3:00 PM", "Mon 6/9 at 10:30 AM", "Thu 3/13 at 2:15 PM",
                              "Wed 12/4 at 9:00 AM", "Fri 8/16 at 4:45 PM"]):
        msg(10, 10, 40 - i * 7, f"Sunrise Dental: Your appointment reminder for {when}. Reply C to confirm.")
        if i < 4:
            msg(10, 10, 40 - i * 7, "C", mine=True, minutes=-5)
    for i, pct in enumerate([40, 25, 50, 30, 60]):
        msg(11, 11, 30 - i * 5, f"FLASH SALE: {pct}% off everything today only! Shop now "
            f"https://bit.ly/deal{i} Reply STOP to opt out")
        if i < 3:
            msg(11, 11, 30 - i * 5, "STOP", mine=True, minutes=-3)
    for i in range(10):                       # an unnamed group of two numbers you never saved
        msg(12, 12 if i % 2 else 13, 16 - i, f"soccer friday? {i}", mine=(i % 3 == 0))
    msg(1, 1, 6, "ok we finally moved to Oakland!!")        # where people live, as they'd say it
    msg(4, 4, 21, "here in brooklyn it's pouring")
    msg(9, 9, 11, "flying to Denver this weekend!")           # a trip, which must not become a location
    msg(9, 9, 3, XSS_MSG)                     # hostile text in a direct thread,
    msg(9, 9, 2.5, XSS_MSG2)                  # where chat history renders it
    db.commit()
    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    for i in range(3):                        # these exist only in the WAL
        msg(1, 1, 0, f"{WAL_MARKER} {i}", mine=(i == 1), minutes=60 - i)
    db.commit()
    return db, path, ab_root
