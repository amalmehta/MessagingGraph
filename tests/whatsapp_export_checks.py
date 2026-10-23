"""Reading Export chat files: the cases a real export can throw at the reader
that the end-to-end fixture's two tidy chats don't.

e2e_checks.py imports an iPhone export and an Android one all the way through.
These are the smallest case of each thing that could go wrong on the way in:
a date that reads two ways, noon and midnight on a 12-hour clock, a name that
is not yours, a file that is not an export. Run from validate.py, which passes
in its `check`.
"""
from __future__ import annotations

import shutil
import tempfile
import zipfile
from pathlib import Path


def run(check) -> None:
    from app.ingest.base import SourceUnavailable
    from app.ingest.whatsapp import WhatsAppSource
    from app.ingest import whatsapp_export as wx
    from app.ingest.whatsapp_export import WhatsAppExportSource, _parse
    from tests.e2e_fixture import JONAS, JONAS_SHOWN, build_whatsapp, build_whatsapp_exports

    root = Path(tempfile.mkdtemp(prefix="mg-wx-"))
    try:
        def export(name: str, text: str, member: str = "_chat.txt") -> str:
            path = root / name
            with zipfile.ZipFile(path, "w") as z:
                z.writestr(member, text)
            return str(path)

        def refuses(paths, me=None) -> str:
            try:
                WhatsAppExportSource(paths, me=me, days=36500).load()
            except SourceUnavailable as e:
                return str(e)
            return ""

        when = lambda text: [ln.when for ln in _parse(text, "x").lines]  # noqa: E731
        both_ways = "[03/04/2026, 10:00:00] A: hi\n[05/04/2026, 10:00:00] B: hey\n"
        check("a day-first file is read day-first when one date can only be read that way",
              when(both_ways + "[25/04/2026, 10:00:00] A: later\n")[0].month == 4)
        check("and month-first when the other way round",
              when(both_ways + "[04/25/2026, 10:00:00] A: later\n")[0].month == 3)
        check("with nothing to decide it, a 24-hour clock means day-first",
              when(both_ways)[0].month == 4)
        check("and a 12-hour clock, the way American phones write it, month-first",
              when("3/4/26, 10:00 AM - A: hi\n")[0].month == 3)
        twelve = when("6/1/26, 12:05 AM - A: late\n6/1/26, 12:05 PM - A: lunch\n")
        check("12 AM is just after midnight and 12 PM is just after noon",
              [t.hour for t in twelve] == [0, 12], str(twelve))
        check("an impossible date is skipped, not guessed",
              when("[31/02/2026, 10:00:00] A: hi\n[01/03/2026, 10:00:00] A: ok\n")
              == [when("[01/03/2026, 10:00:00] A: ok\n")[0]])

        chat = export("WhatsApp Chat - Sam.zip", "[01/03/2026, 10:00:00] Sam: hi\n"
                                                 "[01/03/2026, 10:01:00] Alex: hey\n")
        check("a name that wrote nothing in these chats is refused with a sentence",
              "No message in these chats is from “Robin”" in refuses([chat], me="Robin"))
        group = export("WhatsApp Chat - Crew.zip", "[01/03/2026, 10:00:00] Sam: hi\n"
                                                   "[01/03/2026, 10:01:00] Kim: hey\n")
        check("one group on its own can't say which name is yours, so it asks",
              "--me" in refuses([group]))
        check("and --me answers it",
              not refuses([group], me="Kim"))

        not_zip = root / "WhatsApp Chat - Sam.zip.zip"
        not_zip.write_bytes(b"this is not a zip")
        check("a file that isn't a zip is refused with a sentence",
              "isn't a WhatsApp export zip" in refuses([str(not_zip)]))
        no_chat = root / "photos.zip"
        with zipfile.ZipFile(no_chat, "w") as z:
            z.writestr("IMG-0001.jpg", b"\xff\xd8")
        check("a zip with no chat in it says so",
              "has no chat in it" in refuses([str(no_chat)]))
        was = wx.MAX_CHAT_BYTES
        wx.MAX_CHAT_BYTES = 10
        try:
            check("a chat bigger than any export holds is refused before it is read",
                  "more than any chat export holds" in refuses([chat]))
        finally:
            wx.MAX_CHAT_BYTES = was
        check("a file that isn't there is named",
              "Not found: gone.zip" in refuses([str(root / "gone.zip")]))
        check("the same chat exported twice is read once",
              len(WhatsAppExportSource([chat, chat], days=36500).load().messages) == 2)

        # Someone you never saved is only their number, and keyed by it: the same
        # id the WhatsApp Desktop reader gives them, so both ways in agree.
        key = b"k" * 32
        exp = WhatsAppExportSource(build_whatsapp_exports(str(root / "fx")), days=36500, id_key=key)
        desk = WhatsAppSource(path=build_whatsapp(str(root / "db")), days=36500, id_key=key)
        jonas_export = next(c.id for c in exp.load().contacts.values() if c.name == JONAS_SHOWN)
        check("an unsaved friend has the same id from an export as from WhatsApp Desktop",
              jonas_export == desk._id("w_", JONAS) and jonas_export in desk.load().contacts)
    finally:
        shutil.rmtree(root, ignore_errors=True)
