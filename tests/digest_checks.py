"""The weekly digest and snoozes: which few things come up, that they turn over
each week, that a snooze holds them back exactly as long as it says, and what
the card says when there is nothing to put on it. Run from validate.py, which
passes in its `check`.

Every date here is fixed, never today's, so the rotation lands the same way
whatever week the checks run in.
"""
from __future__ import annotations

import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

MONDAY = datetime(2026, 10, 19, 9, 0)          # a Monday
NOW = datetime(2026, 10, 18, 20, 0)            # the newest message, the night before


def _profile(i: int, *, trend: str = "dormant", recent: float = 0.0, usual: float = 4.0,
             confidence: float = 0.9, state: str = "quiet"):
    from app.models import RelationshipProfile
    return RelationshipProfile(
        contact_id=f"p_{i:02d}", name=f"Person {i:02d}", msgs_per_week=usual,
        msgs_per_week_recent=recent, trend=trend, warmth=50, importance=50, reciprocity=.5,
        initiation_balance=.5, my_median_reply_s=600, their_median_reply_s=600, self_editing=33,
        depth=40, balance=0, state=state, last_contact=NOW - timedelta(days=30 + i),
        confidence=confidence, total_messages=200)


def _plan(i: int, *, state: str = "stale", thread: str | None = None, activity: str | None = None,
          when: datetime | None = None, pid: str | None = None):
    from app.models import Plan
    return Plan(id=pid or f"plan{i:04d}", title=f"{activity or f'thing {i}'} with Someone",
                thread_id=thread or f"t_{i:02d}", thread_name=f"Thread {i}",
                participant_ids=[f"p_{i:02d}"], state=state, confidence=.8,
                first_seen=NOW - timedelta(days=60), last_activity=NOW - timedelta(days=20 + i),
                when_date=when)


def run(check) -> None:
    from app.analysis import digest as dg

    _rotation(check, dg)
    _snoozes(check, dg)
    _wording(check, dg)
    _notification(check, dg)
    _vault(check)
    _api(check, dg)


def _rotation(check, dg) -> None:
    items = list(range(9))
    weeks = [dg.rotate(items, 3, w) for w in range(4)]
    check("rotation: three weeks of nine people never repeat anyone",
          len({x for w in weeks[:3] for x in w}) == 9, str(weeks[:3]))
    check("rotation: and the fourth week comes back round to the first", weeks[3] == weeks[0])
    few = list(range(4))
    check("rotation: with only four to pick from, everyone turns up within two weeks",
          set(dg.rotate(few, 3, 0)) | set(dg.rotate(few, 3, 1)) == set(few))
    check("rotation: picks keep their ranked order inside the week",
          all(w == sorted(w) for w in (dg.rotate(few, 3, k) for k in range(6))))
    check("rotation: fewer than the slots means all of them, every week",
          dg.rotate([1, 2], 3, 0) == dg.rotate([1, 2], 3, 7) == [1, 2])
    check("rotation: an empty list stays empty", dg.rotate([], 3, 5) == [])
    check("rotation: weeks run on across New Year (ISO week 53 into week 1)",
          dg.week_number(date(2027, 1, 4)) - dg.week_number(date(2026, 12, 28)) == 1)
    check("rotation: Monday and the Sunday after are the same week",
          dg.week_number(date(2026, 10, 19)) == dg.week_number(date(2026, 10, 25))
          != dg.week_number(date(2026, 10, 26)))

    people = [_profile(i, recent=i / 10) for i in range(12)]
    plans = [_plan(i) for i in range(8)]
    a = dg.build(people, plans, [], {}, MONDAY, NOW)
    b = dg.build(people, plans, [], {}, MONDAY + timedelta(days=6, hours=14), NOW)
    c = dg.build(people, plans, [], {}, MONDAY + timedelta(days=7), NOW)
    ids = lambda d: [p["id"] for p in d["people"]]          # noqa: E731
    check("digest: at most three people and two plans",
          len(a["people"]) == 3 and len(a["plans"]) == 2, f"{len(a['people'])}, {len(a['plans'])}")
    check("digest: the same set all week, from Monday to Sunday night", ids(a) == ids(b))
    check("digest: a different set the next Monday", not set(ids(a)) & set(ids(c)),
          f"{ids(a)} then {ids(c)}")
    check("digest: plans turn over too",
          not {p["id"] for p in a["plans"]} & {p["id"] for p in c["plans"]})
    reach = {p for w in range(3) for p in ids(dg.build(people, plans, [], {}, MONDAY + timedelta(weeks=w), NOW))}
    check("digest: only the nine furthest-drifted are in the rotation",
          reach == {f"p_{i:02d}" for i in range(9)}, str(sorted(reach)))
    mixed = [_profile(1, trend="steady"), _profile(2, trend="rising"), _profile(3, confidence=.1),
             _profile(4, state="new"), _profile(5, usual=0), _profile(6, trend="fading", recent=1)]
    check("digest: drifted means fading or gone quiet, on a thread there's enough of to read",
          ids(dg.build(mixed, [], [], {}, MONDAY, NOW)) == ["p_06"])
    dated = [_plan(1, when=NOW + timedelta(days=3), state="confirmed"), _plan(2, state="done"),
             _plan(3, state="floated"), _plan(4, state="dropped")]
    check("digest: undated plans only -- one with a day, done or dropped stays off",
          [p["id"] for p in dg.build([], dated, [], {}, MONDAY, NOW)["plans"]] == ["plan0003"])

    class D:                                                  # just enough of a Draft
        def __init__(self, did, target):
            self.id, self.target_ids = did, [target]
    drafts = [D(f"reconnect:p_{i:02d}", f"p_{i:02d}") for i in range(4)]
    one = dg.build([], [], drafts, {}, MONDAY, NOW)
    check("digest: exactly one message", one["message"] is not None and isinstance(one["message"], D))
    check("digest: the message turns over each week",
          len({dg.build([], [], drafts, {}, MONDAY + timedelta(weeks=w), NOW)["message"].id
               for w in range(4)}) == 4)
    check("digest: a draft you put away isn't the week's message",
          dg.build([], [], drafts[:1], {}, MONDAY, NOW, {"reconnect:p_00"})["message"] is None)


def _snoozes(check, dg) -> None:
    check("snooze: a week is seven days", dg.snooze_until("week", MONDAY) == MONDAY + timedelta(days=7))
    check("snooze: a month is thirty", dg.snooze_until("month", MONDAY) == MONDAY + timedelta(days=30))
    check("snooze: for good has no end", dg.snooze_until("forever", MONDAY) is None)
    try:
        dg.snooze_until("fortnight", MONDAY)
        refused = False
    except ValueError:
        refused = True
    check("snooze: a length it doesn't know is refused", refused)

    until = (MONDAY + timedelta(days=7)).isoformat()
    week = {"until": until}
    check("snooze: still on a minute before it runs out",
          dg.is_active(week, MONDAY + timedelta(days=7) - timedelta(minutes=1)))
    check("snooze: and off the moment it does", not dg.is_active(week, MONDAY + timedelta(days=7)))
    check("snooze: for good is still on years later",
          dg.is_active({"until": None}, MONDAY + timedelta(days=3650)))
    check("snooze: one with a date that can't be read lapses rather than hiding someone for ever",
          not dg.is_active({"until": "next tuesday"}, MONDAY))
    check("snooze: only the running ones are live",
          set(dg.live({"person:a": week, "person:b": {"until": None},
                       "person:c": {"until": (MONDAY - timedelta(days=1)).isoformat()}},
                      MONDAY)) == {"person:a", "person:b"})

    people = [_profile(1), _profile(2, recent=.1)]
    snoozed = {dg.person_key("p_01"): {"until": until}}
    held = [p["id"] for p in dg.build(people, [], [], snoozed, MONDAY, NOW)["people"]]
    back = [p["id"] for p in dg.build(people, [], [], snoozed, MONDAY + timedelta(days=7), NOW)["people"]]
    check("snooze: a snoozed person is out of the digest", held == ["p_02"], str(held))
    check("snooze: and back in it once the week is up", "p_01" in back, str(back))
    check("snooze: and out of the drift list itself",
          [p.contact_id for p in dg.drifted(people, set(dg.live(snoozed, MONDAY)))] == ["p_02"])

    first = _plan(1, thread="t_x", activity="Climbing", pid="plan0001")
    again = _plan(1, thread="t_x", activity="climbing", pid="plan0007")
    other = _plan(1, thread="t_y", activity="Climbing")
    check("snooze: a plan keeps its key when the recompute numbers it differently",
          dg.plan_key(first) == dg.plan_key(again))
    check("snooze: the same plan in another chat is a different plan",
          dg.plan_key(first) != dg.plan_key(other))
    month = {dg.plan_key(first): {"until": (MONDAY + timedelta(days=30)).isoformat()}}
    check("snooze: a snoozed plan is out of the digest",
          dg.build([], [again, other], [], month, MONDAY, NOW)["plans"][0]["key"] == dg.plan_key(other)
          and len(dg.build([], [again, other], [], month, MONDAY, NOW)["plans"]) == 1)
    check("snooze: and back after the month",
          len(dg.build([], [again, other], [], month, MONDAY + timedelta(days=30), NOW)["plans"]) == 2)

    class D:
        def __init__(self, did, target):
            self.id, self.target_ids = did, [target]
    check("snooze: no message to a snoozed person in the digest",
          dg.build([], [], [D("reply:p_01", "p_01")], snoozed, MONDAY, NOW)["message"] is None)

    # The drafts list on Now: drift drafts go, an answer they're waiting for stays.
    from app.analysis.messages import build_messages
    from app.models import SELF_ID, Contact, Corpus, Message, RelationshipProfile, Thread
    t_now = datetime(2026, 6, 1, 12)
    msgs = [Message(f"m{i}", "t_q", SELF_ID if i % 2 else "p_q", t_now - timedelta(days=200 - i * 4),
                    "hey how's it going" if i % 2 else "pretty good, you", bool(i % 2)) for i in range(40)]
    corp = Corpus({"p_q": Contact("p_q", "Quinn Ash", "")},
                  {"t_q": Thread("t_q", "Quinn Ash", ("p_q",), False)}, msgs, "test")
    quiet = RelationshipProfile(
        contact_id="p_q", name="Quinn Ash", msgs_per_week=3, msgs_per_week_recent=0, trend="dormant",
        warmth=60, importance=60, reciprocity=.5, initiation_balance=.5, my_median_reply_s=600,
        their_median_reply_s=600, self_editing=33, depth=40, balance=0, state="quiet")
    awake = build_messages(corp, [quiet], [], t_now)
    asleep = build_messages(corp, [quiet], [], t_now, snoozed_people={"p_q"})
    check("snooze: someone gone quiet gets a reconnect draft until they're snoozed",
          [d.kind for d in awake] == ["reconnect"] and asleep == [], f"{[d.kind for d in awake]}")
    asked = msgs + [Message("ask", "t_q", "p_q", t_now - timedelta(days=2), "are you around saturday?", False)]
    corp2 = Corpus(corp.contacts, corp.threads, asked, "test")
    check("snooze: a question they asked you still gets its reply drafted",
          [d.kind for d in build_messages(corp2, [quiet], [], t_now, snoozed_people={"p_q"})] == ["reply"])

    from app.analysis.suggestions import build as build_suggestions
    check("snooze: and no \"it's gone quiet\" observation while snoozed",
          any(s.kind == "reconnect" for s in build_suggestions([quiet], [], t_now))
          and not build_suggestions([quiet], [], t_now, snoozed_people={"p_q"}))


def _wording(check, dg) -> None:
    empty = dg.build([], [], [], {}, MONDAY, NOW)
    check("empty: a week with nothing in it says it's empty", empty["empty"] is True)
    check("empty: and says so as good news",
          empty["empty_line"] == "Nothing to catch up on this week. Nobody you talk to has drifted, "
                                 "and every plan has a date.", empty["empty_line"])
    snoozed = {dg.person_key("p_01"): {"until": None}}
    held = dg.build([_profile(1)], [], [], snoozed, MONDAY, NOW)
    check("empty: a week emptied by snoozes says that instead, not that all is well",
          held["empty"] and held["empty_line"] == "Nothing new this week. The rest is snoozed, and each "
                                                  "one comes back on its own when its snooze runs out.",
          held["empty_line"])
    lapsed = {dg.person_key("p_09"): {"until": (MONDAY - timedelta(days=1)).isoformat()}}
    check("empty: a snooze that has run out doesn't count as holding anything back",
          dg.build([], [], [], lapsed, MONDAY, NOW)["empty_line"] == empty["empty_line"])
    full = dg.build([_profile(1)], [], [], {}, MONDAY, NOW)
    check("empty: a week with something in it has no empty line",
          full["empty"] is False and full["empty_line"] == "")
    check("digest: the label is the Monday it starts", empty["label"] == "Week of 19 October", empty["label"])
    check("digest: each person comes with what changed, in words",
          full["people"][0]["line"] == "You used to talk about 4 times a week. Last message 31 days ago.",
          full["people"][0]["line"])

    line = dg.notification_line
    check("notification: nothing to say sends nothing", line(0, 0, False) is None and empty["notification"] is None)
    check("notification: one of something reads as one",
          line(1, 0, False) == "1 person you've drifted from.", line(1, 0, False))
    check("notification: all three, as a sentence",
          line(3, 2, True) == "3 people you've drifted from, 2 plans without a date and a message worth sending.",
          line(3, 2, True))
    check("notification: two of the three",
          line(0, 1, True) == "1 plan without a date and a message worth sending.", line(0, 1, True))
    check("notification: never a name, only how many",
          "Person" not in (dg.build([_profile(1)], [], [], {}, MONDAY, NOW)["notification"] or "x"))


def _notification(check, dg) -> None:
    on = {"on": True, "weekday": 0, "time": "09:00"}
    due = dg.notify_due
    check("notification: not before the chosen time", not due(on, MONDAY - timedelta(minutes=1), None))
    check("notification: at the chosen time", due(on, MONDAY, None))
    check("notification: a Mac asleep at nine still sends it at ten",
          due(on, MONDAY + timedelta(hours=1), None))
    check("notification: but not half a week late", not due(on, MONDAY + timedelta(days=3), None))
    check("notification: once a week, not every check", not due(on, MONDAY, dg.week_key(MONDAY.date())))
    check("notification: and again the next week", due(on, MONDAY + timedelta(days=7), dg.week_key(MONDAY.date())))
    check("notification: off means off", not due({**on, "on": False}, MONDAY, None))
    check("notification: a Friday-evening choice is Friday evening",
          due({"on": True, "weekday": 4, "time": "18:30"}, datetime(2026, 10, 23, 18, 30), None)
          and not due({"on": True, "weekday": 4, "time": "18:30"}, datetime(2026, 10, 22, 18, 30), None))
    check("notification: a setting that makes no sense falls back to off, Monday, nine",
          dg.clean_notify({"on": "", "weekday": 12, "time": "25:99"})
          == {"on": False, "weekday": 0, "time": "09:00"}
          and dg.clean_notify(None) == {"on": False, "weekday": 0, "time": "09:00"})


def _vault(check) -> None:
    from app.models import Contact, Corpus, Message, SELF_ID, Thread
    from app.overrides import OverrideStore, Overrides
    from app.vault import MemoryKeyStore, Vault

    root = Path(tempfile.mkdtemp(prefix="mg-snooze-")) / "vault"
    vault = Vault(root, MemoryKeyStore())
    corp = Corpus({"p_secret": Contact("p_secret", "Someone", "+15550100")},
                  {"t": Thread("t", "Someone", ("p_secret",), False)},
                  [Message("m", "t", SELF_ID, NOW, "hi", True)], "test")
    vault.write(corp, {})
    store = OverrideStore(vault)
    o = Overrides()
    o.snoozed["person:p_secret"] = {"until": None, "for": "forever", "at": NOW.isoformat(), "label": "Someone"}
    store.save(o)
    raw = b"".join(f.read_bytes() for f in root.iterdir() if f.is_file())
    check("vault: a snooze survives being saved and read back",
          store.load().snoozed == o.snoozed)
    check("vault: and is sealed -- neither the id nor the name is readable on disk",
          b"p_secret" not in raw and b"forever" not in raw)
    check("vault: an older overrides file with no snoozes still loads",
          Overrides.from_dict({"names": {}}).snoozed == {}
          and Overrides.from_dict({"snoozed": "junk"}).snoozed == {})
    vault.wipe()


def _api(check, dg) -> None:
    from fastapi.testclient import TestClient
    from app import main as server
    from app.security import COOKIE

    c = TestClient(server.app, base_url="http://127.0.0.1:8077")
    c.cookies.set(COOKIE, server.guard.session)
    o = {"origin": "http://127.0.0.1:8077"}
    saved = server.state.overrides.load()
    before = len(server.state.gate.audit)
    try:
        d = c.get("/api/digest").json()
        check("api: the demo has a week to show",
              not d["empty"] and 0 < len(d["people"]) <= 3 and len(d["plans"]) <= 2,
              f"{len(d['people'])} people, {len(d['plans'])} plans")
        check("api: and working it out made no call off this Mac",
              len(server.state.gate.audit) == before)
        who = d["people"][0]
        r = c.post("/api/snooze", json={"kind": "person", "id": who["id"], "length": "month"}, headers=o)
        check("api: snoozing someone says until when",
              r.status_code == 200 and "snoozed until" in r.json()["note"], r.text[:200])
        after = c.get("/api/digest").json()
        check("api: a snoozed person is out of this week",
              who["id"] not in [p["id"] for p in after["people"]])
        check("api: and listed as snoozed, so it can be taken back",
              [s["id"] for s in after["snoozed"]] == [who["id"]] and after["snoozed"][0]["for"] == "month")
        drafts = c.get("/api/messages").json()
        check("api: no reconnect or check-in draft for them on Now",
              not any(x["target_ids"] == [who["id"]] and x["kind"] in dg.DRIFT_DRAFTS for x in drafts))
        check("api: nor an observation that they've gone quiet",
              not any(s["target_id"] == who["id"] and s["kind"] in ("reconnect", "check_in")
                      for s in c.get("/api/overview").json()["suggestions"]))
        c.post("/api/snooze", json={"kind": "person", "id": who["id"], "length": None}, headers=o)
        check("api: taking it back puts them back",
              who["id"] in [p["id"] for p in c.get("/api/digest").json()["people"]]
              and c.get("/api/digest").json()["snoozed"] == [])

        # A snooze from a month ago that has run out holds nothing back.
        o2 = server.state.overrides.load()
        o2.snoozed[dg.person_key(who["id"])] = {"until": (datetime.now() - timedelta(minutes=1)).isoformat(),
                                                "for": "month", "label": who["name"]}
        server.state.overrides.save(o2)
        lapsed = c.get("/api/digest").json()
        check("api: once a snooze runs out they're back, without anyone touching it",
              who["id"] in [p["id"] for p in lapsed["people"]] and lapsed["snoozed"] == [])

        if d["plans"]:
            plan = d["plans"][0]
            c.post("/api/snooze", json={"kind": "plan", "id": plan["id"], "length": "week"}, headers=o)
            gone = c.get("/api/digest").json()
            check("api: a snoozed plan is out of this week",
                  plan["key"] not in [p["key"] for p in gone["plans"]]
                  and gone["snoozed"][0]["id"] == plan["key"])
            c.post("/api/snooze", json={"kind": "plan", "id": plan["key"], "length": None}, headers=o)
            check("api: and comes back by its key", c.get("/api/digest").json()["snoozed"] == [])

        check("api: a snooze length it doesn't know is refused in words",
              c.post("/api/snooze", json={"kind": "person", "id": who["id"], "length": "fortnight"},
                     headers=o).status_code == 400)
        check("api: so is a thing that isn't a person or a plan",
              c.post("/api/snooze", json={"kind": "group", "id": "x", "length": "week"},
                     headers=o).status_code == 400)
        check("api: and someone who isn't there",
              c.post("/api/snooze", json={"kind": "person", "id": "p_nobody", "length": "week"},
                     headers=o).status_code == 404)
        check("api: another website can't snooze anyone",
              c.post("/api/snooze", json={"kind": "person", "id": who["id"], "length": "week"},
                     headers={"origin": "http://attacker.example"}).status_code == 403)

        n = c.post("/api/digest/notify", json={"on": True, "weekday": 4, "time": "18:30"}, headers=o)
        check("api: the notification time is kept",
              n.status_code == 200 and c.get("/api/digest/notify").json()
              == {"on": True, "weekday": 4, "time": "18:30"}
              and server.appdata.load()["digest_notify"]["time"] == "18:30")
        check("api: a time that isn't one is refused",
              c.post("/api/digest/notify", json={"time": "7pm"}, headers=o).status_code == 400
              and c.post("/api/digest/notify", json={"weekday": 7}, headers=o).status_code == 400)
        c.post("/api/digest/notify", json={"on": False}, headers=o)
        js = Path("app/static/app.js").read_text()
        check("api: the page draws the card, the snooze menu and the notification setting",
              "drawWeek" in js and "select class=\"snooze\"" in js and "/api/digest/notify" in js)
    finally:
        server.state.overrides.save(saved)
