"""A whole, fake history for the end-to-end check: a chat.db, its AddressBook,
and WhatsApp Desktop's ChatStorage.sqlite.

chatdb_fixture.py is about how chat.db is shaped. This one is about what is in
it: the cases recent fixes were for, each said the way a person would say it.

* Group messages credited to whoever wrote them -- an iMessage group where each
  friend writes on their own handle, and a WhatsApp group where only the member
  row says who wrote a message (and one row has none, and must be dropped).
* Phone numbers from outside the US and IBANs, in the recent messages a draft
  would carry, so the outbound preview has to hide them.
* café and cafe, Cyrillic and Japanese, for search.
* A plan gone stale, one revived by being raised again, and a group plan a
  friend's yes confirms.
* A WhatsApp history that is imported on its own, with no iMessage beside it.

Every name, number and IBAN is invented: the numbers sit in ranges set aside
for fiction or are made up, and the IBANs are the published examples.

Everything is dated from ANCHOR, not from today, so the analysis -- which
measures from the newest message -- reads the same history whenever it runs.

    .venv/bin/python -m tests.e2e_fixture /some/empty/folder    # to look at it
"""
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timedelta

from app.ingest.imessage import local_to_apple
from app.ingest.whatsapp import CORE_DATA_EPOCH

ANCHOR = datetime(2026, 6, 1, 18, 0)          # a Monday evening
# Far enough back that the import window, which is measured from today, takes
# every message here for the next several years.
DAYS = 4000

# People, and the handles iMessage knows them by.
PRIYA, MARCUS, LEA, DMITRI, YUKI = 1, 2, 3, 4, 5
IM_PEOPLE = {
    PRIYA: ("Priya", "Raman", "(415) 555-0142", "+14155550142"),
    MARCUS: ("Marcus", "Webb", "+1 415-555-0188", "+14155550188"),
    LEA: ("Léa", "Dubois", "+33 6 12 34 56 78", "+33612345678"),
    DMITRI: ("Дмитрий", "Волков", "+7 916 123-45-67", "+79161234567"),
    YUKI: ("Yuki", "Tanaka", "+81 90-1234-5678", "+819012345678"),
}
GROUP = 6                                       # "Climbing crew": Priya, Marcus, Léa

# Identifiers that must never leave in a draft.
LEA_PHONE = "+33 6 12 34 56 78"
LEA_IBAN = "FR76 3000 6000 0112 3456 7890 189"
YUKI_PHONE = "+81 90-1234-5678"
AARAV_PHONE = "+91 98765 43210"
AARAV_IBAN = "DE89370400440532013000"
SECRETS = (LEA_PHONE, LEA_IBAN, YUKI_PHONE, AARAV_PHONE, AARAV_IBAN,
           "33612345678", "919876543210", "3456 7890")

# What friends say when nothing is being planned. None of it names an activity,
# so the only plans in this history are the ones put there on purpose.
CHATTER = [
    ("how's your week going", "honestly not bad, busy though"),
    ("did you see that article i sent", "yes!! wild"),
    ("ha that's so true", "right?"),
    ("thinking of you today", "aw thank you"),
    ("work is a lot right now", "ugh, hang in there"),
    ("my sister says hi", "say hi back!"),
]


def _at(days_ago: float, hour: int = 19, minute: int = 0) -> datetime:
    return (ANCHOR - timedelta(days=days_ago)).replace(hour=hour, minute=minute)


def _addressbook(root: str) -> None:
    os.makedirs(root, exist_ok=True)
    db = sqlite3.connect(os.path.join(root, "AddressBook-v22.abcddb"))
    db.executescript("""
        CREATE TABLE ZABCDRECORD (Z_PK INTEGER PRIMARY KEY, ZFIRSTNAME TEXT,
            ZLASTNAME TEXT, ZNICKNAME TEXT, ZORGANIZATION TEXT);
        CREATE TABLE ZABCDPHONENUMBER (ZOWNER INTEGER, ZFULLNUMBER TEXT);
        CREATE TABLE ZABCDEMAILADDRESS (ZOWNER INTEGER, ZADDRESS TEXT);
    """)
    for pk, (first, last, shown, _) in IM_PEOPLE.items():
        db.execute("INSERT INTO ZABCDRECORD VALUES (?,?,?,NULL,NULL)", (pk, first, last))
        db.execute("INSERT INTO ZABCDPHONENUMBER VALUES (?,?)", (pk, shown))
    db.commit()
    db.close()


def build_chatdb(root: str) -> tuple[str, str]:
    """Write chat.db and its AddressBook under `root`. Returns (chat_db, ab_root)."""
    os.makedirs(root, exist_ok=True)
    ab_root = os.path.join(root, "AddressBook")
    _addressbook(ab_root)
    path = os.path.join(root, "chat.db")
    db = sqlite3.connect(path)
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
    for h, (*_, handle) in IM_PEOPLE.items():
        db.execute("INSERT INTO handle VALUES (?,?,'iMessage')", (h, handle))
        db.execute("INSERT INTO chat VALUES (?,?,45,NULL,?)", (h, f"iMessage;-;{handle}", handle))
        db.execute("INSERT INTO chat_handle_join VALUES (?,?)", (h, h))
    db.execute("INSERT INTO chat VALUES (?, 'iMessage;+;chat-climb', 43, 'Climbing crew', 'chat-climb')",
               (GROUP,))
    for h in (PRIYA, MARCUS, LEA):
        db.execute("INSERT INTO chat_handle_join VALUES (?,?)", (GROUP, h))

    n = [0]

    def say(chat: int, who: int | None, when: datetime, text: str) -> None:
        """`who` is the handle that wrote it, or None for me."""
        n[0] += 1
        date = local_to_apple(when)
        mine = who is None
        db.execute("INSERT INTO message (ROWID, guid, text, handle_id, date, is_from_me, account, "
                   "destination_caller_id) VALUES (?,?,?,?,?,?,?,?)",
                   (n[0], f"E2E-{n[0]}", text, 0 if mine else who, date, int(mine),
                    "E:me@icloud.com" if mine else None, "me@icloud.com" if mine else None))
        db.execute("INSERT INTO chat_message_join VALUES (?,?,?)", (chat, n[0], date))

    def chat(h: int, days_ago: float, k: int, me_first: bool = True) -> None:
        a, b = CHATTER[k % len(CHATTER)]
        first, second = (None, h) if me_first else (h, None)
        say(h, first, _at(days_ago), a)
        say(h, second, _at(days_ago, 19, 4), b)

    # Priya: every three days for a year, right up to now. Close, steady.
    for k, d in enumerate(range(360, 0, -3)):
        chat(PRIYA, d, k, me_first=k % 2 == 0)
    say(PRIYA, PRIYA, _at(2, 12), "found a new café on Valencia, the cortado is unreal")

    # Marcus: every other day, until four months ago, then nothing. The drift.
    for k, d in enumerate(range(360, 124, -2)):
        chat(MARCUS, d, k, me_first=k % 3 != 0)
    # A coffee nobody followed up on: stale.
    say(MARCUS, None, _at(124, 20), "we should get coffee sometime")
    say(MARCUS, MARCUS, _at(124, 20, 5), "for sure")

    # Léa: every other day until six weeks ago, then once. Fading, not gone.
    for k, d in enumerate(list(range(360, 45, -2)) + [20]):
        chat(LEA, d, k, me_first=k % 2 == 1)
    say(LEA, LEA, _at(5, 9), "On se retrouve au café Belleville jeudi ?")
    say(LEA, LEA, _at(4, 10), f"mon nouveau numéro : {LEA_PHONE}")
    say(LEA, LEA, _at(4, 10, 2), f"et pour le loyer, mon IBAN : {LEA_IBAN}")
    say(LEA, None, _at(4, 10, 6), "merci, noté")

    # Dmitri: weekly, in Russian. A dinner floated, forgotten, then raised again.
    for k, d in enumerate(range(357, 0, -7)):
        chat(DMITRI, d, k, me_first=k % 2 == 0)
    say(DMITRI, DMITRI, _at(9, 21), "где встреча в субботу?")
    say(DMITRI, None, _at(9, 21, 3), "у метро, в десять")
    say(DMITRI, None, _at(20, 18), "we should grab dinner sometime")
    say(DMITRI, None, _at(1, 18), "we should grab dinner sometime, for real")

    # Yuki: a fortnightly thread, in Japanese and English.
    for k, d in enumerate(range(350, 0, -14)):
        chat(YUKI, d, k, me_first=k % 2 == 1)
    say(YUKI, YUKI, _at(6, 8), "来月東京で会おう！")
    say(YUKI, YUKI, _at(6, 8, 1), f"new number btw, {YUKI_PHONE}")

    # The group: each friend on their own handle, so each is credited for theirs.
    for k, d in enumerate(range(300, 3, -10)):
        for i, who in enumerate((PRIYA, LEA, None) if d < 124 else (PRIYA, MARCUS, LEA, None)):
            say(GROUP, who, _at(d, 20, i * 3), CHATTER[(k + i) % len(CHATTER)][i % 2])
    say(GROUP, LEA, _at(3, 18), "climbing saturday?")
    say(GROUP, PRIYA, _at(3, 18, 4), "yes i'm in!")

    db.commit()
    db.close()
    return path, ab_root


# WhatsApp ----------------------------------------------------------------------
GROUP_JID = "120363041234567890@g.us"
AARAV = "919876543210@s.whatsapp.net"       # India
JONAS = "4915112345678@s.whatsapp.net"      # Germany
SOFIA = "34612345678@s.whatsapp.net"        # Spain


def build_whatsapp(root: str) -> str:
    """Write a ChatStorage.sqlite under `root`, the store WhatsApp Desktop keeps.
    Returns its path."""
    os.makedirs(root, exist_ok=True)
    path = os.path.join(root, "ChatStorage.sqlite")
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE ZWAPROFILEPUSHNAME (ZJID TEXT, ZPUSHNAME TEXT);
        CREATE TABLE ZWACHATSESSION (Z_PK INTEGER PRIMARY KEY, ZCONTACTJID TEXT,
                                     ZPARTNERNAME TEXT, ZSESSIONTYPE INTEGER);
        CREATE TABLE ZWAGROUPMEMBER (Z_PK INTEGER PRIMARY KEY, ZCHATSESSION INTEGER,
                                     ZMEMBERJID TEXT, ZCONTACTNAME TEXT, ZFIRSTNAME TEXT);
        CREATE TABLE ZWAMESSAGE (Z_PK INTEGER PRIMARY KEY, ZCHATSESSION INTEGER,
                                 ZISFROMME INTEGER, ZMESSAGEDATE REAL, ZTEXT TEXT,
                                 ZMESSAGETYPE INTEGER, ZGROUPMEMBER INTEGER,
                                 ZFROMJID TEXT, ZPUSHNAME TEXT);
    """)
    # Jonas has no name anywhere but the one he gave himself.
    db.execute("INSERT INTO ZWAPROFILEPUSHNAME VALUES (?, 'Jonas')", (JONAS,))
    db.executemany("INSERT INTO ZWACHATSESSION VALUES (?,?,?,?)", [
        (1, AARAV, "Aarav Shah", 0),
        (2, GROUP_JID, "Berlin flatmates", 1),
        (3, "status@broadcast", "", 0),                 # not a conversation
    ])
    db.executemany("INSERT INTO ZWAGROUPMEMBER VALUES (?,?,?,?,?)", [
        (1, 2, AARAV, "Aarav Shah", None),
        (2, 2, JONAS, None, None),
        (3, 2, SOFIA, "Sofía García", None),
    ])
    n = [0]

    def say(session: int, member: int | None, when: datetime, text: str,
            mine: bool = False, kind: int = 0, push: str | None = None) -> None:
        n[0] += 1
        frm = GROUP_JID if session == 2 else (None if mine else AARAV)
        db.execute("INSERT INTO ZWAMESSAGE VALUES (?,?,?,?,?,?,?,?,?)",
                   (n[0], session, int(mine), when.timestamp() - CORE_DATA_EPOCH, text,
                    kind, member, None if mine else frm, push))

    # Aarav, directly: twice a week for a year.
    for k, d in enumerate(range(364, 0, -4)):
        a, b = CHATTER[k % len(CHATTER)]
        say(1, None, _at(d, 13), a, mine=k % 2 == 0, push=None if k % 2 == 0 else "Aarav")
        say(1, None, _at(d, 13, 3), b, mine=k % 2 == 1, push=None if k % 2 == 1 else "Aarav")
    say(1, None, _at(3, 9), f"changed my number, it's {AARAV_PHONE} now", push="Aarav")
    say(1, None, _at(3, 9, 1), f"rent goes to {AARAV_IBAN}", push="Aarav")

    # The group. ZFROMJID is the group's own id and ZPUSHNAME is whoever wrote
    # it, so the only honest answer to "who wrote this" is ZGROUPMEMBER.
    who = {1: "Aarav", 2: "Jonas", 3: "Sofía"}
    for k, d in enumerate(range(200, 2, -6)):
        for i, member in enumerate((1, 2, 3, None)):
            text = CHATTER[(k + i) % len(CHATTER)][i % 2]
            say(2, member, _at(d, 21, i * 2), text, mine=member is None,
                push=who.get(member))
    say(2, 3, _at(4, 11), "nos vemos en el café de la esquina", push="Sofía")
    say(2, 2, _at(2, 19), "dinner friday?", push="Jonas")
    say(2, 3, _at(2, 19, 6), "yes i'm in", push="Sofía")
    # No member row: nobody can say who wrote it, so it must not be anyone's.
    say(2, None, _at(2, 20), "who wrote this one", push="Jonas")
    # Someone joining, recorded as a message.
    say(2, None, _at(30, 10), "179735841767521@lid", kind=6)
    db.commit()
    db.close()
    return path


# The same two WhatsApp chats, as Export chat on a phone writes them --------------
ME = "Alex Rivera"                          # who these exports belong to; said nowhere
# A phone that never saved Jonas shows his number where his name would be.
JONAS_SHOWN = "+49 151 12345678"
LRM, NNBSP = "‎", " "


def _iphone(when: datetime, who: str, text: str) -> str:
    return f"[{when:%d/%m/%Y, %H:%M:%S}] {who}: {text}"


def _android_us(when: datetime, who: str | None, text: str) -> str:
    clock = f"{when.hour % 12 or 12}:{when:%M}{NNBSP}{'AM' if when.hour < 12 else 'PM'}"
    stamp = f"{when.month}/{when.day}/{when:%y}, {clock}"
    return f"{stamp} - {who}: {text}" if who else f"{stamp} - {text}"


def build_whatsapp_exports(root: str) -> list[str]:
    """Write two Export chat zips under `root`: Aarav as an iPhone exports a
    chat, and the flatmates' group as an American Android phone does. Returns
    their paths."""
    import zipfile
    os.makedirs(root, exist_ok=True)

    # An iPhone: "_chat.txt", day-first, 24-hour, direction marks before notices.
    lines = [_iphone(_at(364, 12, 59), "Aarav Shah", LRM + "Messages and calls are end-to-end "
                     "encrypted. No one outside of this chat, not even WhatsApp, can read or "
                     "listen to them.")]
    for k, d in enumerate(range(364, 0, -4)):
        a, b = CHATTER[k % len(CHATTER)]
        first, second = (ME, "Aarav Shah") if k % 2 == 0 else ("Aarav Shah", ME)
        lines += [_iphone(_at(d, 13), first, a), _iphone(_at(d, 13, 3), second, b)]
    lines += [
        _iphone(_at(10, 8), "Aarav Shah", LRM + "image omitted"),
        _iphone(_at(10, 8, 1), "Aarav Shah", "the view from the new flat"),
        "three rooms, one of them is yours whenever you visit",      # the same message, wrapped
        _iphone(_at(9, 8), ME, f"that looks amazing {LRM}<This message was edited>"),
        _iphone(_at(9, 8, 2), "Aarav Shah", LRM + "This message was deleted."),
        _iphone(_at(3, 9), "Aarav Shah", f"changed my number, it's {AARAV_PHONE} now"),
        _iphone(_at(3, 9, 1), "Aarav Shah", f"rent goes to {AARAV_IBAN}"),
    ]
    aarav = os.path.join(root, "WhatsApp Chat - Aarav Shah.zip")
    with zipfile.ZipFile(aarav, "w") as z:
        z.writestr("_chat.txt", "\n".join(lines) + "\n")
        z.writestr("00000012-PHOTO-2026-05-22-08-00-00.jpg", b"\xff\xd8 not really a photo")

    # An American Android phone: the chat named in the file, month-first, 12-hour
    # with a narrow space before PM, and its notices written with no one's name.
    members = ("Aarav Shah", JONAS_SHOWN, "Sofía García", ME)
    lines = [_android_us(_at(201, 9), None, "Messages and calls are end-to-end encrypted. No one "
                         "outside of this chat, not even WhatsApp, can read or listen to them. "
                         "Tap to learn more."),
             _android_us(_at(201, 9, 1), None, f"{JONAS_SHOWN} added Sofía García")]
    for k, d in enumerate(range(200, 2, -6)):
        for i, who in enumerate(members):
            lines.append(_android_us(_at(d, 21, i * 2), who, CHATTER[(k + i) % len(CHATTER)][i % 2]))
    lines += [
        _android_us(_at(30, 10), "Sofía García", "<Media omitted>"),
        _android_us(_at(4, 11), "Sofía García", "nos vemos en el café de la esquina"),
        _android_us(_at(2, 19), JONAS_SHOWN, "dinner friday?"),
        _android_us(_at(2, 19, 6), "Sofía García", "yes i'm in"),
    ]
    group = os.path.join(root, "WhatsApp Chat with Berlin flatmates.zip")
    with zipfile.ZipFile(group, "w") as z:
        z.writestr("WhatsApp Chat with Berlin flatmates.txt", "\n".join(lines) + "\n")
    return [aarav, group]


def build(root: str) -> dict:
    chatdb, ab = build_chatdb(os.path.join(root, "imessage"))
    return {"chatdb": chatdb, "addressbook": ab,
            "whatsapp": build_whatsapp(os.path.join(root, "whatsapp")),
            "whatsapp_exports": build_whatsapp_exports(os.path.join(root, "whatsapp-export"))}


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "e2e-fixture"
    for k, v in build(out).items():
        print(f"{k:12} {v}")
