"""The whole way through: import the fake history in tests/e2e_fixture.py into a
vault, serve it, and read back what the app shows.

`mg import` runs for real -- chat.db, then WhatsApp beside it -- into a vault
in a temporary folder whose key lives in a stand-in for the Keychain. The app
then serves that vault and is asked, over its own HTTP API, for the people,
who has drifted, the open plans, what a draft would send, and what search
finds. A second vault holds WhatsApp alone.

The answers are compared with tests/snapshots/e2e.json, so a change anywhere
between the reader and the page shows up as a difference here. The named
checks first say which fix broke, if one did; the snapshot catches the rest.
When a difference is the one you meant, see tests/README.md for how to
regenerate it.

Run from validate.py, which passes in its `check`, or on its own:

    .venv/bin/python -m tests.e2e_checks            # compare
    .venv/bin/python -m tests.e2e_checks --update   # rewrite the snapshot
"""
from __future__ import annotations

import contextlib
import difflib
import io
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

SNAPSHOT = Path(__file__).parent / "snapshots" / "e2e.json"
SEARCHES = ("cafe", "café", "встреча", "東京", "climbing")
# Ranked worst first: the share of their usual rate someone is at lately.
DRIFTING = ("dormant", "fading")


def _people(client) -> list[dict]:
    return sorted(({"name": p["name"], "state": p["state"], "trend": p["trend"],
                    "messages": p["total_messages"], "per_week": round(p["msgs_per_week"], 1),
                    "per_week_recent": round(p["msgs_per_week_recent"], 1),
                    "open_plans": p["open_plans"]}
                   for p in client.get("/api/people").json()), key=lambda p: p["name"])


def _drift(people: list[dict]) -> list[str]:
    gone = [p for p in people if p["trend"] in DRIFTING and p["per_week"]]
    return [p["name"] for p in sorted(gone, key=lambda p: (p["per_week_recent"] / p["per_week"],
                                                           p["name"]))]


def _plans(client, names: dict[str, str]) -> list[dict]:
    return [{"title": p["title"], "state": p["state"], "when": p["when_text"],
             "assumed": p["assumed"], "with": sorted(names[c] for c in p["participant_ids"])}
            for p in client.get("/api/plans").json()]


def _search(client, q: str) -> list[dict]:
    hits = client.get("/api/search", params={"q": q}).json()["hits"]
    return [{"kind": h["kind"], "title": h["title"], "detail": h["detail"]} for h in hits[:5]]


def _preview(client, cid: str) -> dict:
    p = client.get(f"/api/privacy/preview/{cid}").json()
    return {"redactions": p["redactions"],
            "said": [line for line in p["would_send"].splitlines()
                     if line.startswith(("me:", "Léa", "Yuki", "Aarav"))][-3:]}


def _group_credit(state, title: str) -> dict[str, int]:
    """Who wrote what in one group, by name, from what the app is serving."""
    c = state.corpus
    tid = next(t.id for t in c.threads.values() if t.name == title)
    out: dict[str, int] = {}
    for m in c.messages_in(tid):
        who = "me" if m.is_from_me else c.contacts[m.sender_id].name
        out[who] = out.get(who, 0) + 1
    return dict(sorted(out.items()))


def _reading(client, main_mod, group: str, previews: tuple[str, ...]) -> dict:
    ov = client.get("/api/overview").json()
    names = {c["id"]: c["name"] for c in client.get("/api/contacts").json()}
    by_name = {v: k for k, v in names.items()}
    people = _people(client)
    return {
        "source": ov["source"],
        "counts": {"messages": ov["messages"], "people": ov["contacts"], "threads": ov["threads"]},
        "people": people,
        "drift": _drift(people),
        "suggestions": [{"kind": s["kind"], "who": s["who"]} for s in ov["suggestions"]],
        "plans": _plans(client, names),
        "open_plans": ov["plans"]["open"],
        "group_credit": _group_credit(main_mod.state, group),
        "redaction": {n: _preview(client, by_name[n]) for n in previews},
        "search": {q: _search(client, q) for q in SEARCHES},
    }


def collect(fx: dict[str, str], root: Path) -> tuple[dict, dict]:
    """Import, serve and read the fixture. Returns (snapshot, live) -- what is
    compared with the stored snapshot, and what the named checks also need."""
    import app.ingest.whatsapp as wa_mod
    import app.main as main_mod
    import types
    import app.vault as vault_mod
    from fastapi.testclient import TestClient
    from app import cli
    from app.security import COOKIE
    from app.state import AppState
    from app.vault import MemoryKeyStore, Vault
    from tests.e2e_fixture import DAYS

    saved = (vault_mod.KeychainKeyStore, Vault.__init__.__defaults__,
             wa_mod.WhatsAppSource.__init__.__defaults__, main_mod.state, vault_mod.subprocess)
    # Excluding a throwaway folder from Time Machine is not what is under test,
    # and `tmutil` can take ten seconds a call to say yes.
    vault_mod.subprocess = types.SimpleNamespace(run=lambda *a, **k: None)
    # WhatsApp is read from where WhatsApp keeps it; point that at the fixture.
    wa_mod.WhatsAppSource.__init__.__defaults__ = (fx["whatsapp"], 400, None)
    client = TestClient(main_mod.app, base_url="http://127.0.0.1:8077")
    client.cookies.set(COOKIE, main_mod.guard.session)

    def use_vault(path: Path) -> MemoryKeyStore:
        keys = MemoryKeyStore()                 # never the real Keychain
        vault_mod.KeychainKeyStore = lambda: keys
        Vault.__init__.__defaults__ = (path, None)
        return keys

    def mg(*args: str) -> int:
        with contextlib.redirect_stdout(io.StringIO()):
            return cli.main(["import", "--days", str(DAYS), *args])

    live: dict = {}
    try:
        keys = use_vault(root / "vault")
        live["imported_imessage"] = mg("--chatdb", fx["chatdb"], "--addressbook", fx["addressbook"])
        live["imported_whatsapp"] = mg("--whatsapp")
        live["platforms"] = Vault().imported()
        live["sealed"] = b"".join(f.read_bytes() for f in (root / "vault").glob("*.enc"))
        live["key_in_stub"] = keys.get() is not None

        main_mod.state = AppState("vault")
        imessage = _reading(client, main_mod, "Climbing crew", ("Léa Dubois", "Yuki Tanaka"))
        live["raw_imessage"] = [m.text for m in main_mod.state.corpus.messages]
        switched = client.post("/api/platform", json={"platform": "whatsapp"},
                               headers={"origin": "http://127.0.0.1:8077"})
        live["switched"] = switched.status_code
        whatsapp = _reading(client, main_mod, "Berlin flatmates", ("Aarav Shah",))
        live["raw_whatsapp"] = [m.text for m in main_mod.state.corpus.messages]

        # Someone who has only ever imported WhatsApp, the import that needs no
        # Full Disk Access.
        use_vault(root / "vault-whatsapp-only")
        live["only_imported"] = mg("--whatsapp")
        only = Vault()
        live["only_platforms"], live["only_source"] = only.imported(), only.source()
        main_mod.state = AppState(only.source())
        alone = _reading(client, main_mod, "Berlin flatmates", ("Aarav Shah",))
        alone["platforms"] = client.get("/api/platforms").json()

        # Someone whose WhatsApp is on their phone, who exported two chats.
        use_vault(root / "vault-whatsapp-export")
        live["export_imported"] = mg("--whatsapp-export", *fx["whatsapp_exports"])
        exported = Vault()
        live["export_manifest"] = exported.manifest("whatsapp") or {}
        main_mod.state = AppState(exported.source())
        export = _reading(client, main_mod, "Berlin flatmates", ("Aarav Shah",))
        live["raw_export"] = [m.text for m in main_mod.state.corpus.messages]
        from app.refresh import refresh_once
        live["export_refreshed"] = refresh_once()
        live["export_after_refresh"] = len(Vault().read("whatsapp").messages)
    finally:
        (vault_mod.KeychainKeyStore, Vault.__init__.__defaults__,
         wa_mod.WhatsAppSource.__init__.__defaults__, main_mod.state, vault_mod.subprocess) = saved
    return {"imessage": imessage, "whatsapp": whatsapp, "whatsapp_only": alone,
            "whatsapp_export": export}, live


def _diff(want: dict, got: dict) -> str:
    a = json.dumps(want, indent=1, ensure_ascii=False, sort_keys=True).splitlines()
    b = json.dumps(got, indent=1, ensure_ascii=False, sort_keys=True).splitlines()
    lines = list(difflib.unified_diff(a, b, "snapshot", "now", lineterm="", n=1))
    return "\n".join(lines[:40]) + ("\n      …" if len(lines) > 40 else "")


def run(check, update: bool = False) -> None:
    from tests.e2e_fixture import SECRETS, build

    root = Path(tempfile.mkdtemp(prefix="mg-e2e-"))
    try:
        snap, live = collect(build(str(root / "fixture")), root)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    im, wa, only = snap["imessage"], snap["whatsapp"], snap["whatsapp_only"]
    ex = snap["whatsapp_export"]

    # --- the import -------------------------------------------------------------
    check("`mg import` reads the fixture's chat.db, then its WhatsApp, into one vault",
          live["imported_imessage"] == live["imported_whatsapp"] == 0
          and live["platforms"] == ["imessage", "whatsapp"], str(live["platforms"]))
    check("with its key in the stand-in Keychain, and no name or text in the clear on disk",
          live["key_in_stub"] and not any(s in live["sealed"] for s in
                                           (b"Priya", "Léa".encode(), b"Aarav", b"cortado")))

    # --- each fix, by name --------------------------------------------------------
    check("iMessage: each friend in a group is credited with what they wrote",
          {k: v > 0 for k, v in im["group_credit"].items()}
          == {"Léa Dubois": True, "Marcus Webb": True, "Priya Raman": True, "me": True},
          str(im["group_credit"]))
    # 33 rounds of chatter each; Jonas also asked about dinner, Sofía answered it
    # and mentioned the café. Handing the sender-less row to the first member
    # listed would give Aarav 34.
    check("WhatsApp: group messages go to the member who wrote them, not the first listed",
          wa["group_credit"] == {"Aarav Shah": 33, "Jonas": 34, "Sofía García": 35, "me": 33},
          str(wa["group_credit"]))
    check("and a group message with no member row is no one's",
          "who wrote this one" not in live["raw_whatsapp"]
          and "179735841767521@lid" not in live["raw_whatsapp"])
    previews = {**im["redaction"], **wa["redaction"]}
    leaked = [s for s in SECRETS if any(s in line for p in previews.values() for line in p["said"])]
    check("a draft's preview hides French, Japanese and Indian numbers, and IBANs",
          not leaked and "[iban]" in previews["Léa Dubois"]["redactions"]
          and "[phone]" in previews["Yuki Tanaka"]["redactions"]
          and {"[phone]", "[iban]"} <= set(previews["Aarav Shah"]["redactions"]), str(leaked))
    check("though the numbers are still there to read in the history itself",
          any("+33 6 12 34 56 78" in t for t in live["raw_imessage"]))
    hit = lambda reading, q, needle: any(needle in h["title"] for h in reading["search"][q])  # noqa: E731
    check("search: cafe finds café, and café finds it too",
          hit(im, "cafe", "café") and hit(im, "café", "café") and hit(wa, "cafe", "café"))
    check("search: Cyrillic and Japanese find their messages",
          hit(im, "встреча", "встреча") and hit(im, "東京", "東京"))
    states = {p["title"]: p["state"] for p in im["plans"]}
    check("a coffee nobody followed up on is stale; a dinner raised again is not",
          states.get("Coffee with Marcus Webb") == "stale"
          and states.get("Dinner with Дмитрий Волков") == "floated", str(states))
    check("a friend's yes confirms a group plan, on both platforms",
          states.get("Climbing with Climbing crew") == "confirmed"
          and any(p["state"] == "confirmed" and "Berlin" in p["title"] for p in wa["plans"]))
    check("the friend you stopped talking to drifts furthest, then the one fading",
          im["drift"][:2] == ["Marcus Webb", "Léa Dubois"], str(im["drift"]))
    check("a vault with only WhatsApp in it is imported, and served as WhatsApp",
          live["only_imported"] == 0 and live["only_platforms"] == ["whatsapp"]
          and live["only_source"] == "vault:whatsapp" and only["source"] == "whatsapp"
          and only["people"] == wa["people"], f"{live['only_platforms']} {live['only_source']}")

    # --- WhatsApp from Export chat zips ---------------------------------------------
    from tests.e2e_fixture import JONAS_SHOWN
    check("`mg import --whatsapp-export` reads an iPhone export and an Android one",
          live["export_imported"] == 0 and ex["counts"]["threads"] == 2
          and live["export_manifest"].get("from") == "export", str(ex["counts"]))
    # The same 33 rounds as the database. Sofía also sent a photo, mentioned
    # the café and said yes; Jonas, whose number is all the phone knew, asked.
    check("you are told apart from your friends without being asked, and each is credited",
          ex["group_credit"] == {"Aarav Shah": 33, JONAS_SHOWN: 34, "Sofía García": 36, "me": 33},
          str(ex["group_credit"]))
    raw = live["raw_export"]
    check("WhatsApp's own notices, and a deleted message, are not messages",
          not any("end-to-end encrypted" in t or "added Sofía" in t or "was deleted" in t
                  for t in raw))
    check("a photo still counts, with no text; a wrapped message is one message",
          raw.count("") == 2 and "the view from the new flat\nthree rooms, one of them is yours "
                                  "whenever you visit" in raw)
    check("and an edit leaves the message, not WhatsApp's note about it",
          "that looks amazing" in raw and not any("was edited" in t for t in raw))
    check("an exported chat is redacted, searched and planned like any other",
          {"[phone]", "[iban]"} <= set(ex["redaction"]["Aarav Shah"]["redactions"])
          and hit(ex, "cafe", "café")
          and any(p["state"] == "confirmed" and "Berlin" in p["title"] for p in ex["plans"]))
    check("the daily refresh leaves an export alone rather than replacing it",
          live["export_refreshed"] == [] and live["export_after_refresh"] == ex["counts"]["messages"],
          str(live["export_refreshed"]))

    # --- everything else, against the snapshot --------------------------------------
    if update or not SNAPSHOT.exists():
        SNAPSHOT.parent.mkdir(exist_ok=True)
        SNAPSHOT.write_text(json.dumps(snap, indent=1, ensure_ascii=False, sort_keys=True) + "\n")
        check("snapshot written", True, str(SNAPSHOT.relative_to(Path.cwd())
                                             if SNAPSHOT.is_relative_to(Path.cwd()) else SNAPSHOT))
        return
    want = json.loads(SNAPSHOT.read_text())
    for part in ("imessage", "whatsapp", "whatsapp_only", "whatsapp_export"):
        same = want.get(part) == snap[part]
        check(f"{part}: people, drift, plans, redaction and search match the snapshot", same,
              "" if same else "\n" + _diff(want.get(part), snap[part]))


if __name__ == "__main__":
    os.environ.setdefault("MG_HOME", tempfile.mkdtemp(prefix="mg-home-"))
    fails: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}{'  — ' + detail if detail else ''}")
        if not ok:
            fails.append(name)

    run(check, update="--update" in sys.argv[1:])
    print(f"\n{len(fails)} FAILED" if fails else "\nall checks passed")
    sys.exit(1 if fails else 0)
