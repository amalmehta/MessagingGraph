#!/usr/bin/env python
"""Check the analyzer against the generator's hidden parameters.

The generator is configured with behaviours (how warm someone is, how much I
soften my language with them, how often plans land). The analyzer never sees
those numbers -- it only reads text and timestamps. This asserts that what comes
out the far end actually recovers what went in, which is the only reason to
trust any of the numbers the UI displays.

    .venv/bin/python validate.py
"""
from __future__ import annotations

import math
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from statistics import correlation

from app.analysis.plans import extract_plans, follow_through, open_plan_counts
from app.analysis.relationships import profile_all
from app.ingest.synthetic import PERSONAS, WEEKS, SyntheticSource

FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{'  — ' + detail if detail else ''}")
    if not ok:
        FAILS.append(name)


def corr(name: str, xs: list[float], ys: list[float], floor: float) -> None:
    r = correlation(xs, ys)
    check(f"{name} correlates with ground truth (r >= {floor})", r >= floor, f"r = {r:.2f}")


def main() -> int:
    corpus = SyntheticSource().load()
    plans = extract_plans(corpus)
    profiles = profile_all(corpus, open_plans=open_plan_counts(plans),
                           follow_through=follow_through(plans))
    by_id = {p.contact_id: p for p in profiles}
    persona = {p.id: p for p in PERSONAS}
    ids = [p.id for p in PERSONAS if p.id in by_id]

    print(f"\ncorpus: {len(corpus.messages)} messages, {len(corpus.contacts)} contacts, "
          f"{len(plans)} plans\n")

    print("recovering hidden persona parameters:")
    corr("self-editing", [persona[i].my_hedging for i in ids],
         [by_id[i].self_editing for i in ids], 0.75)
    corr("warmth", [persona[i].their_warmth for i in ids],
         [by_id[i].warmth for i in ids], 0.6)
    # The persona's `rate` is a *baseline*; the observed volume is that baseline
    # scaled by the trend multiplier over the window, by how long the persona was
    # active, and by burst length. Comparing against the raw baseline would be
    # testing the analyzer on a number the generator never actually produced.
    def effective(p):
        mult = 1.0 if abs(p.trend - 1) < 1e-9 else (p.trend - 1) / math.log(p.trend)
        active = 1.0 if p.dormant_after is None else (p.dormant_after + 1) / WEEKS
        burst = 3 + (6 + p.depth * 8) / 2
        return p.rate * mult * active * burst

    corr("message volume", [effective(persona[i]) for i in ids],
         [by_id[i].msgs_per_week for i in ids], 0.85)
    corr("their reply latency", [persona[i].their_latency for i in ids],
         [by_id[i].their_median_reply_s for i in ids], 0.7)
    corr("depth", [persona[i].depth for i in ids], [by_id[i].depth for i in ids], 0.6)

    print("\nconnection states on the personas designed to be caught:")
    check("the thread the user over-edits in reads as uneven",
          by_id["p_jordan"].state == "uneven", f"Jordan → {by_id['p_jordan'].state}")
    check("the one-sided thread reads as uneven",
          by_id["p_dani"].state == "uneven", f"Dani → {by_id['p_dani'].state}")
    check("the logistics-only thread is not mistaken for closeness",
          by_id["p_theo"].state == "practical", f"Theo → {by_id['p_theo'].state}")
    check("the closest friend reads as close",
          by_id["p_priya"].state == "close", f"Priya → {by_id['p_priya'].state}")
    check("the thin thread declines to characterise the friendship",
          by_id["p_owen"].state == "new",
          f"Owen → {by_id['p_owen'].state} at {by_id['p_owen'].confidence:.2f} confidence")
    check("a friendship that went silent is described as quiet, not judged",
          by_id["p_hana"].state == "quiet", f"Hana → {by_id['p_hana'].state}")

    print("\nthe app does not grade people:")
    states = {p.state for p in profiles}
    check("no state passes judgement on a person",
          states <= {"close", "steady", "practical", "uneven", "quiet", "new", "group"},
          ", ".join(sorted(states)))
    check("every signal offers an ordinary explanation alongside the number",
          all(sig.benign for p in profiles for sig in p.signals),
          f"{sum(len(p.signals) for p in profiles)} signals, all with a benign reading")
    loaded = " ".join(p.headline for p in profiles).lower()
    banned = ["taking more than it returns", "costing", "toxic", "drain",
              "you should", "one-way", "managing them", "not worth"]
    hits = [w for w in banned if w in loaded]
    check("no headline tells the user a friendship is bad for them",
          not hits, "found: " + ", ".join(hits) if hits else "none of the loaded phrasings")

    print("\ntrend detection:")
    check("the fading friendship is flagged",
          by_id["p_marcus"].trend in ("fading", "dormant"), f"Marcus → {by_id['p_marcus'].trend}")
    check("the friend who went silent is flagged",
          by_id["p_hana"].trend == "dormant", f"Hana → {by_id['p_hana'].trend}")
    check("the new friendship is flagged as rising",
          by_id["p_sofia"].trend == "rising", f"Sofia → {by_id['p_sofia'].trend}")

    print("\nself-editing is measured against my own baseline, not absolutely:")
    j, p_ = by_id["p_jordan"].self_editing, by_id["p_priya"].self_editing
    check("softening in the hedged thread far exceeds the closest one",
          j > p_ * 2.5, f"Jordan {j:.0f} vs Priya {p_:.0f}")

    print("\nplan extraction:")
    states = {s: sum(1 for p in plans if p.state == s) for s in
              ("floated", "proposed", "confirmed", "scheduled", "stale", "done", "dropped")}
    print(f"  states: {states}")
    check("stale plans are found", states["stale"] > 10, f"{states['stale']} stale")
    check("plans reach a locked date sometimes", states["done"] + states["scheduled"] > 20)
    check("no duplicate plan ids", len({p.id for p in plans}) == len(plans))
    ft = follow_through(plans)
    check("the low-follow-through persona lands fewer plans than the high one",
          ft["p_leo"] < ft["p_amara"], f"Leo {ft['p_leo']:.0%} vs Amara {ft['p_amara']:.0%}")
    # Follow-through is the weakest of the metrics: the extractor reads intent from
    # text, and enthusiasm is easier to detect than a date that quietly never got set.
    # So this asserts a real correlation, not an exact ranking it cannot support.
    fids = [i for i in ids if i in ft]
    corr("plan follow-through", [persona[i].followthrough for i in fids],
         [ft[i] for i in fids], 0.45)

    print("\nsuggestions:")
    from app.analysis.suggestions import build as build_suggestions
    sugg = build_suggestions(profiles, plans)
    check("there is something to suggest", len(sugg) >= 4, f"{len(sugg)} suggestions")
    check("every suggestion names one concrete thing you could do",
          all(s.action and s.observation for s in sugg))
    check("good news is surfaced too, not only problems",
          any(s.tone == "warm" for s in sugg),
          f"{sum(1 for s in sugg if s.tone == 'warm')} warm of {len(sugg)}")
    counts: dict[str, int] = {}
    for s in sugg:
        counts[s.who] = counts.get(s.who, 0) + 1
    check("no single person is listed more than once",
          max(counts.values(), default=0) <= 1)
    sj = next((s for s in sugg if s.who == "Jordan Feld"), None)
    check("a signal about the user produces advice aimed at the user",
          sj is not None and sj.kind == "reflect",
          f"Jordan → {sj.kind if sj else 'absent'}")

    print("\nmessaging-rate series:")
    from app.analysis.series import contact_series, overall_series
    now = datetime.now()
    ov = overall_series(corpus, now, weeks=40)
    charted = sum(b["total"] for b in ov["buckets"])
    window = sum(1 for m in corpus.messages if (now - m.ts).days < 280)
    check("every message in the window lands in exactly one bucket",
          charted == window, f"{charted} charted vs {window} in range")
    check("buckets are split into sender and receiver",
          all(b["mine"] + b["theirs"] == b["total"] for b in ov["buckets"]))

    sof = contact_series(corpus, "p_sofia", now, weeks=40)
    pre = [b for b in sof["buckets"] if b["end"] <= sof["first_message"]]
    check("a new contact's pre-history is empty and marked, not drawn as silence",
          sof["first_message"] is not None and all(b["total"] == 0 for b in pre),
          f"{len(pre)} weeks before you met")
    check("the rising contact's recent rate beats the prior quarter",
          (sof["summary"]["change"] or 0) > 20, f"{sof['summary']['change']}%")
    mar = contact_series(corpus, "p_marcus", now, weeks=40)
    check("the dormant contact's rate collapses",
          (mar["summary"]["change"] or 0) < -50, f"{mar['summary']['change']}%")

    print("\nchat history:")
    from fastapi.testclient import TestClient
    from app.main import app as fastapi_app, guard, state as app_state
    from app.security import COOKIE
    client = TestClient(fastapi_app, base_url="http://127.0.0.1:8077")
    client.cookies.set(COOKIE, guard.session)

    def page(**kw):
        return client.get("/api/people/p_jordan/history", params=kw).json()

    first = page(limit=40)
    seen, before, pages = set(), first["oldest"], 1
    seen |= {m["id"] for m in first["messages"]}
    overlaps = 0
    while True:
        nxt = page(limit=40, before=before)
        if not nxt["messages"]:
            break
        ids = {m["id"] for m in nxt["messages"]}
        overlaps += len(seen & ids)
        seen |= ids
        pages += 1
        if not nxt["has_more"]:
            break
        before = nxt["oldest"]
    check("paging back through history reaches every message",
          len(seen) == first["total"], f"{len(seen)} of {first['total']} over {pages} pages")
    check("pages never repeat a message", overlaps == 0)
    check("messages arrive oldest-first within a page",
          all(a["ts"] <= b["ts"] for a, b in
              zip(first["messages"], first["messages"][1:])))
    check("each message carries the gap that splits conversations",
          all("gap" in m for m in first["messages"]))

    lower, upper = page(q="rain check"), page(q="RAIN CHECK")
    check("search is case-insensitive",
          lower["matched"] == upper["matched"] > 0, f"{lower['matched']} matches")
    check("search narrows without changing the thread total",
          lower["total"] == first["total"] and lower["matched"] < first["total"])
    check("a search with no hits returns nothing rather than everything",
          page(q="zzzznotpresent")["matched"] == 0)

    print("\nvibes:")
    from app.analysis.vibes import mood_of
    from app.state import AppState
    st = AppState("synthetic")
    vp = {p.contact_id: p for p in st.profiles}
    check("laughter and affection read as warm, not negative",
          mood_of("I'm crying 😂") > 0.2 and mood_of("miss you") > 0.2 and mood_of("I'm dead 😭😭") > 0.2)
    check("politeness and banter read as neutral, not sad",
          mood_of("sorry to bother you but") == 0 and mood_of("stop") == 0)
    check("grief reads as heavy, even with a 'lol' attached",
          mood_of("my grandma passed away last night") < -0.2
          and mood_of("honestly so overwhelmed and sad lately lol") < -0.2)
    check("slang praise isn't mistaken for illness, and illness still reads as heavy",
          mood_of("that concert was sick") > 0.2 and mood_of("ugh I am sick in bed") < 0)
    r_mood = correlation([pp.their_warmth for pp in PERSONAS],
                         [vp[pp.id].vibe.mood_theirs for pp in PERSONAS])
    check("their mood tracks generated warmth (r >= 0.7)", r_mood >= 0.7, f"r = {r_mood:.2f}")
    vnames = [p.vibe.name for p in st.profiles]
    check("every relationship gets its own description", len(set(vnames)) == len(vnames),
          f"{len(set(vnames))} distinct of {len(vnames)}")
    check("descriptions stay brief", all(len(p.vibe.line) <= 110 for p in st.profiles),
          f"longest {max(len(p.vibe.line) for p in st.profiles)} chars")
    check("the logistics thread's vibe is about getting things done", "practical" in vp["p_theo"].vibe.top)
    check("the long late-night thread's vibe is about depth", "deep" in vp["p_nadia"].vibe.top)
    vcopy = " ".join(f"{p.vibe.name} {p.vibe.line} {p.vibe.mood_note}" for p in st.profiles).lower()
    vhits = [w for w in banned + ["bonnie", "clyde", "couple", "soulmate", "lovers"] if w in vcopy]
    check("vibe copy follows the tone rules, with no romantic or famous pairings",
          not vhits, "found: " + ", ".join(vhits) if vhits else "clean")
    check("mood never changes anyone's connection state",
          {p.contact_id: p.state for p in profiles} == {p.contact_id: p.state for p in st.profiles})

    print("\nplans, connections and proposals:")
    from app.analysis.circles import activity_of, connections, plans_overview
    from app.analysis.proposals import propose
    ov = plans_overview(st.corpus, st.plans, st.now)
    k = ov["counts"]
    bucketed = [r["id"] for key in ("confirmed", "in_motion", "revive") for r in ov[key]]
    check("every plan lands in exactly one bucket",
          len(bucketed) == len(set(bucketed)) and sum(k[x] for x in
          ("confirmed", "in_motion", "revive", "lapsed", "done", "dropped")) == len(st.plans),
          f"{len(st.plans)} plans")
    check("'worth reviving' only covers the last 90 days",
          all(r["days_idle"] <= 90 for r in ov["revive"]) and k["lapsed"] > 0,
          f"{k['revive']} recent, {k['lapsed']} lapsed")
    cx = connections(st.corpus, st.plans, st.now)
    circle = {c["name"]: {m["name"].split()[0] for m in c["members"]} for c in cx["circles"]}
    check("each group lists exactly its members",
          circle.get("Dinner Club") == {"Priya", "Amara", "Rafa"} and circle.get("Family") == {"Iris", "Owen"})
    rafa_links = {x["name"].split()[0]: x["via"] for x in cx["by_contact"]["p_rafa"]}
    check("a connection names the group that makes it",
          rafa_links.get("Priya") == ["Dinner Club"] and rafa_links.get("Theo") == ["Climbing Crew"])
    check("people with no shared group are listed as one-to-one only",
          set(cx["solo"]) == {"Chris Vandermeer", "Dani Okafor", "Hana Kobayashi", "Jordan Feld"})
    groups = {t.name: t for t in st.corpus.threads.values() if t.is_group}
    dc = groups["Dinner Club"]
    props = [propose(st.corpus, st.plans, list(dc.participant_ids), dc.id, sd, st.now) for sd in range(6)]
    dc_acts = {activity_of(p).lower() for p in st.plans if p.thread_id == dc.id}
    check("group proposals come from that group's own plan history",
          all(p.activity in dc_acts or p.basis == "fresh" for p in props),
          ", ".join(sorted({p.activity for p in props})))
    outsiders = {c.name.split()[0] for c in st.corpus.contacts.values()} - {"Priya", "Amara", "Rafa"}
    check("a group draft never names anyone outside the group",
          not any(o in p.draft for p in props for o in outsiders))
    check("proposed days leave at least three days to reply",
          all((p.when.date() - st.now.date()).days >= 3 for p in props))
    check("'another idea' gives a different draft", len({p.draft for p in props}) > 1)
    rdm = st.corpus.direct_thread("p_rafa")
    solo_p = propose(st.corpus, st.plans, ["p_rafa"], rdm.id, 0, st.now)
    dm_acts = {activity_of(p).lower() for p in st.plans if p.thread_id == rdm.id}
    check("a one-on-one proposal only revives plans from that one-on-one thread",
          solo_p.basis != "revive" or solo_p.activity in dm_acts, solo_p.activity)
    pair_p = propose(st.corpus, st.plans, ["p_priya", "p_rafa"], None, 0, st.now)
    check("drafts greet one person, two people and a group correctly",
          solo_p.draft.startswith("Hey Rafa") and pair_p.draft.startswith("Hey you two")
          and props[0].draft.startswith("Hey all"))

    print("\nmessages worth sending:")
    from app.analysis.messages import Voice, build_messages, restyle
    from app.models import Contact as _Contact, Corpus as _Corpus, Message as _Message
    from app.models import RelationshipProfile as _RP, SELF_ID as _SELF, Thread as _Thread
    drafts = build_messages(st.corpus, st.profiles, st.plans, st.now)
    check("drafts exist and are ranked by value",
          len(drafts) >= 4 and all(a.value >= b.value for a, b in zip(drafts, drafts[1:])),
          f"{len(drafts)} drafts")
    solo_targets = [d.target_ids[0] for d in drafts if d.kind != "group_plan"]
    check("at most one draft per person", len(solo_targets) == len(set(solo_targets)))
    check("the warm friend who went quiet gets a reconnect draft",
          any(d.kind == "reconnect" and "p_hana" in d.target_ids for d in drafts))
    top = drafts[0]
    again = next(d for d in build_messages(st.corpus, st.profiles, st.plans, st.now, {top.id: 1})
                 if d.id == top.id)
    check("'another version' changes the wording", again.draft != top.draft)
    check("drafts follow how you text that person",
          restyle("Hey Priya! How's your week going?", Voice("yo", True, False, ""))
          == "yo priya, how's your week going?")

    def tiny(extra, name="Sam Lee"):
        t_now = datetime(2026, 9, 1, 18, 0)
        msgs = [_Message(f"x{i}", "t_x", _SELF if i % 2 else "p_x", t_now - timedelta(days=31 - i),
                         "hey how's it going" if i % 2 else "pretty good, you", bool(i % 2))
                for i in range(20)]
        msgs += [_Message(f"e{j}", "t_x", _SELF if mine else "p_x", t_now - timedelta(days=days), text, mine)
                 for j, (days, text, mine) in enumerate(extra)]
        msgs.sort(key=lambda m: m.ts)
        corp = _Corpus({"p_x": _Contact("p_x", name, "")},
                       {"t_x": _Thread("t_x", name, ("p_x",), False)}, msgs, "test")
        prof = _RP(contact_id="p_x", name=name, msgs_per_week=5, msgs_per_week_recent=5,
                   trend="steady", warmth=60, importance=70, reciprocity=.5, initiation_balance=.5,
                   my_median_reply_s=600, their_median_reply_s=600, self_editing=33, depth=40,
                   balance=30, state="close")
        return build_messages(corp, [prof], [], t_now)

    waiting = tiny([(20, "my job interview is next tuesday, so nervous", False),
                    (2, "are you coming on saturday?", False)])
    check("someone waiting on your answer comes first",
          waiting and waiting[0].kind == "reply" and "saturday" in waiting[0].detail)
    follow = tiny([(20, "my job interview is next tuesday, so nervous", False), (12, "anyway gotta run", False)])
    check("something they told you about gets a follow-up",
          follow and follow[0].kind == "follow_up" and "interview" in follow[0].draft)
    medical = tiny([(20, "my doctor's appointment is tuesday", False), (12, "anyway gotta run", False)])
    check("medical mentions never get an automatic follow-up",
          not any(d.kind == "follow_up" for d in medical))
    asked = tiny([(20, "my job interview is next tuesday", False), (15, "good luck at the interview!!", True),
                  (12, "anyway gotta run", False)])
    check("no follow-up once you've already asked about it",
          not any(d.kind == "follow_up" for d in asked))
    numbered = tiny([(20, "my job interview is next tuesday, so nervous", False),
                     (12, "anyway gotta run", False)], name="(408) 555-0111")
    check("drafts never greet someone by their phone number",
          bool(numbered) and numbered[0].draft.lower().split()[0] in ("hey,", "hey!")
          and "(408" not in numbered[0].draft and "0111" not in numbered[0].draft,
          numbered[0].draft if numbered else "no draft")

    print("\nreal message store (fixture shaped like chat.db):")
    from app.ingest.imessage import IMessageSource
    from tests.chatdb_fixture import LONG_BODY, WAL_MARKER, build
    tmp = tempfile.mkdtemp(prefix="mg-fixture-")
    writer, cpath, ab = build(tmp)
    legacy = sqlite3.connect(f"file:{cpath}?mode=ro&immutable=1", uri=True)
    legacy_wal = legacy.execute("SELECT count(*) FROM message WHERE text LIKE ?",
                                (WAL_MARKER + "%",)).fetchone()[0]
    legacy.close()
    src = IMessageSource(path=cpath, ab_root=ab, days=400)
    rc = src.load()
    writer.close()
    rep, texts = src.report, [m.text for m in rc.messages]
    check("rows still in the write-ahead log are imported",
          sum(t.startswith(WAL_MARKER) for t in texts) == 3,
          f"an immutable=1 open sees {legacy_wal} of 3")
    check("bodies stored only in attributedBody are recovered",
          rep.from_attributed_body == 11 and LONG_BODY in texts,
          f"{rep.from_attributed_body} recovered, long multibyte body intact")
    target = [m.reactions for m in rc.messages if m.text == "can't wait to see you!"]
    check("tapbacks become reactions, and a removed tapback is removed",
          target == [("love",)] and not any(t.startswith(("Loved", "Laughed", "Removed"))
                                           for t in texts))
    check("2FA short codes and never-answered numbers are dropped",
          not any("verification code" in t or "CRUISE" in t for t in texts))
    names = [c.name for c in rc.contacts.values()]
    check("an unknown number you actually talk to is kept",
          any(n.startswith("(415) 555-0199") for n in names))
    check("one person across SMS, iMessage and email is one contact",
          names.count("Priya Raman") == 1 and rep.handles_merged == 1)
    priya_group = next(t for t in rc.threads.values() if t.is_group
                       and any(rc.contacts[c].name == "Priya Raman" for c in t.participant_ids))
    check("your own Apple ID inside a group is not a contact", len(priya_group.participant_ids) == 2)
    check("unsent messages, group events and anything outside the window are dropped",
          "oops wrong chat" not in texts and rep.dropped["group events"] == 1
          and not any("ancient history" in t for t in texts))
    check("attachment-only messages count toward volume but carry no text",
          texts.count("") == 1)
    wal_ts = [m.ts for m in rc.messages if m.text.startswith(WAL_MARKER)]
    check("timestamps are local wall-clock time, not UTC",
          all(timedelta(minutes=50) < datetime.now() - t < timedelta(minutes=70) for t in wal_ts))
    ids = list(rc.contacts) + list(rc.threads) + [m.id for m in rc.messages]
    # Test for the real property: no actual handle (full number, its last seven
    # digits, an email or its local part) inside any id. Matching a pattern like
    # "555" instead flags random hex digits in the HMAC and says nothing about leaks.
    fixture_handles = [h for (h,) in sqlite3.connect(cpath).execute("SELECT id FROM handle")]
    leaks = set()
    for h in fixture_handles:
        if "@" in h:
            leaks |= {h.lower(), h.split("@")[0].lower()}
        else:
            digits = "".join(ch for ch in h if ch.isdigit())
            leaks |= {digits, digits[-7:]} if len(digits) >= 7 else {digits}
    check("no phone number or email appears in any id",
          not any(x and x in i.lower() for i in ids for x in leaks) and not any("@" in i for i in ids),
          f"{len(ids)} ids checked against {len(fixture_handles)} handles")

    print("\nencrypted vault:")
    from app.vault import MemoryKeyStore, Vault, VaultError
    vroot = Path(tmp) / "vault"
    keys = MemoryKeyStore()
    vault = Vault(vroot, keys)
    vault.write(rc, {"messages": len(rc.messages)})
    raw = vault.corpus_path.read_bytes()
    needles = ["group chatter 1", "Priya Raman", "4155550142", LONG_BODY[:30]]
    check("no message text, name or number is readable on disk",
          not any(n.encode() in raw for n in needles), f"{len(raw):,} bytes of ciphertext")
    import stat as _stat
    modes = (oct(_stat.S_IMODE(vroot.stat().st_mode)),
             oct(_stat.S_IMODE(vault.corpus_path.stat().st_mode)))
    check("folder 0700, file 0600, excluded from Spotlight",
          modes == ("0o700", "0o600") and (vroot / ".metadata_never_index").exists(),
          f"{modes[0]} / {modes[1]}")
    back = vault.read()
    check("decrypts back to the identical corpus",
          back.messages == rc.messages and back.threads == rc.threads
          and back.contacts == rc.contacts)
    from cryptography.fernet import Fernet
    try:
        Vault(vroot, MemoryKeyStore(Fernet.generate_key().decode())).read()
        wrong_key_ok = False
    except VaultError:
        wrong_key_ok = True
    check("the wrong key fails closed", wrong_key_ok)
    vault.wipe()
    check("wipe removes the data and the key", not vroot.exists() and keys.get() is None)
    shutil.rmtree(tmp, ignore_errors=True)

    print("\nlocalhost hardening:")
    anon = TestClient(fastapi_app, base_url="http://127.0.0.1:8077")
    check("the API refuses a browser that hasn't opened the launch link",
          anon.get("/api/overview").status_code == 401)
    check("a DNS-rebinding Host header is refused",
          client.get("/api/overview", headers={"host": "attacker.example:8077"}).status_code == 403)
    r = client.post("/api/privacy", json={"local_only": False},
                    headers={"origin": "http://attacker.example"})
    check("another website can't switch local-only off",
          r.status_code == 403 and app_state.settings.local_only)
    check("a POST with no Origin at all is refused",
          client.post("/api/privacy", json={"local_only": False}).status_code == 403)
    guessed = TestClient(fastapi_app, base_url="http://127.0.0.1:8077")
    guessed.cookies.set(COOKIE, "guess")
    check("a guessed session cookie is refused", guessed.get("/api/overview").status_code == 401)
    r = anon.get(f"/?token={guard.token}", follow_redirects=False)
    sc = r.headers.get("set-cookie", "").lower()
    check("the launch link sets an HttpOnly, SameSite=Strict cookie and strips the token",
          r.status_code == 303 and "httponly" in sc and "samesite=strict" in sc
          and r.headers.get("location") == "/")
    check("a wrong launch token is refused",
          anon.get("/?token=nope", follow_redirects=False).status_code == 401)
    h = client.get("/api/overview").headers
    check("responses forbid inline script, framing and caching",
          "script-src 'self';" in h.get("content-security-policy", "")
          and h.get("x-frame-options") == "DENY" and h.get("cache-control") == "no-store")
    check("interactive API docs and the schema are not served",
          client.get("/api/docs").status_code == 404
          and client.get("/openapi.json").status_code == 404)

    print("\nautomated vs real conversations:")
    from app.analysis.spam import NEVER_FILTER_AFTER, classify, classify_thread
    tmp2 = tempfile.mkdtemp(prefix="mg-spam-")
    w2, cp2, ab2 = build(tmp2)
    fx = IMessageSource(path=cp2, ab_root=ab2, days=400).load()
    w2.close()
    fv = classify(fx)
    marketing = next(v for v in fv.values() if v.name.endswith("555-0144"))
    check("a marketing number you replied STOP to is set aside, with reasons",
          marketing.automated and any("opt-out" in r for r in marketing.reasons), f"score {marketing.score}")
    dental = next((v for v in fv.values() if v.name == "Sunrise Dental"), None)
    check("appointment reminders from a business card are set aside",
          dental is not None and dental.automated and any("business" in r for r in dental.reasons))
    sam_v = next(v for v in fv.values() if v.name.endswith("555-0199"))
    check("an unknown number you really talk to is kept", not sam_v.automated, f"score {sam_v.score}")
    check("no demo conversation is mistaken for automated",
          not any(v.automated for v in classify(corpus).values()))
    t0 = datetime(2026, 9, 1)
    lk_msgs = []
    for i in range(30):
        lk_msgs.append(_Message(f"l{i}", "t_l", "p_l", t0 + timedelta(minutes=2 * i),
                                f"lol look at this https://bit.ly/x{i % 2}", False))
        lk_msgs.append(_Message(f"m{i}", "t_l", _SELF, t0 + timedelta(minutes=2 * i + 1),
                                f"haha no way, that's amazing {i}", True))
    lk = _Corpus({"p_l": _Contact("p_l", "Link Friend", "")},
                 {"t_l": _Thread("t_l", "Link Friend", ("p_l",), False)}, lk_msgs, "t")
    lk_v = classify_thread(lk, lk.threads["t_l"])
    check(f"someone you've written {NEVER_FILTER_AFTER}+ real messages to is never filtered",
          not lk_v.automated, f"score {lk_v.score} despite links and repeats")

    print("\nnames you give, and your corrections:")
    from app.overrides import OverrideStore, apply_names, clean_name, is_unnamed
    check("names are cleaned: trimmed, no control characters",
          clean_name("  Sam   Rivera" + chr(7) + " ") == "Sam Rivera")
    try:
        clean_name("   ")
        blank_refused = False
    except ValueError:
        blank_refused = True
    check("a blank name is refused with a message", blank_refused)
    unnamed_c = sorted((c for c in fx.contacts.values() if is_unnamed(c)), key=lambda c: c.name)
    check("real contacts with no name are recognised",
          [c.name for c in unnamed_c] == ["(408) 555-0111", "(408) 555-0222", "(415) 555-0144", "(415) 555-0199"])
    sam = next(c for c in unnamed_c if c.name.endswith("0199"))
    renamed = apply_names(fx, {sam.id: "Sam Rivera"})
    check("a name you give renames the person and their thread",
          renamed.contacts[sam.id].name == "Sam Rivera"
          and renamed.direct_thread(sam.id).name == "Sam Rivera"
          and not is_unnamed(renamed.contacts[sam.id]))
    g408 = next(t for t in fx.threads.values() if t.is_group
                and all(fx.contacts[c].name.startswith("(408)") for c in t.participant_ids))
    check("a group with no name is titled by its members, not their area codes",
          set(g408.name.split(", ")) == {"…0111", "…0222"}, g408.name)
    by_num = {fx.contacts[c].name[-4:]: c for c in g408.participant_ids}
    puds_muds = {by_num["0111"]: "puds", by_num["0222"]: "muds"}
    check("naming the people in a group updates the group's title",
          set(apply_names(fx, puds_muds).threads[g408.id].name.split(", ")) == {"puds", "muds"},
          apply_names(fx, puds_muds).threads[g408.id].name)
    from dataclasses import replace as _replace
    legacy = _Corpus(fx.contacts, {**fx.threads, g408.id: _replace(g408, name="(408), (408)")}, fx.messages, "t")
    check("a title an older import baked in is recognised and fixed",
          set(apply_names(legacy, {}).threads[g408.id].name.split(", ")) == {"…0111", "…0222"}
          and set(apply_names(legacy, puds_muds).threads[g408.id].name.split(", ")) == {"puds", "muds"})
    five = {f"p_{i}": _Contact(f"p_{i}", f"({a}) 555-01{i}{i}", "")
            for i, a in enumerate(["657", "408", "415", "510", "650"])}
    fixed5 = apply_names(_Corpus(five, {"g_5": _Thread("g_5", "(657), (408), (415) +2", tuple(five), True)},
                                 [], "t"), {})
    check("a baked-in title for a bigger group ('+2') is fixed too",
          fixed5.threads["g_5"].name == "…0100, …0111, …0122 +2", fixed5.threads["g_5"].name)
    vk = MemoryKeyStore()
    v2 = Vault(Path(tmp2) / "vault", vk)
    v2.write(fx, {"messages": len(fx.messages)})
    store = OverrideStore(v2)
    ov2 = store.load()
    ov2.names[sam.id] = "Sam Rivera"
    ov2.real.add(dental.thread_id)
    store.save(ov2)
    sealed = (Path(tmp2) / "vault" / "overrides.v1.enc").read_bytes()
    check("your corrections are saved encrypted, not in plain text", b"Sam Rivera" not in sealed)
    back_o = OverrideStore(Vault(Path(tmp2) / "vault", vk)).load()
    check("and they come back after a restart",
          back_o.names.get(sam.id) == "Sam Rivera" and dental.thread_id in back_o.real)
    shutil.rmtree(tmp2, ignore_errors=True)

    print("\nconnections beyond group chats:")
    from app.analysis.network import enrich, mentions
    tn = datetime(2026, 9, 1)
    mc = {k: _Contact(k, v, "") for k, v in {"p_a": "Priya Raman", "p_b": "Rafa Duarte", "p_c": "Will Chen",
                                              "p_d": "Sam Lee", "p_e": "Sam Ortiz"}.items()}
    mt = {f"t_{k}": _Thread(f"t_{k}", mc[k].name, (k,), False) for k in mc}
    lines = [("t_p_a", "p_a", "did you see rafa last night"), ("t_p_a", _SELF, "yes! rafa says hi"),
             ("t_p_a", "p_a", "will you come saturday"), ("t_p_a", "p_a", "will you bring snacks"),
             ("t_p_b", "p_b", "sam is coming too"), ("t_p_b", "p_b", "sam lee is coming"),
             ("t_p_b", "p_b", "sam lee said so")]
    mm = [_Message(str(i), tid, sid, tn + timedelta(minutes=i), text, sid == _SELF)
          for i, (tid, sid, text) in enumerate(lines)]
    ment = mentions(_Corpus(mc, mt, mm, "t"))
    check("someone named in another person's chat becomes a connection",
          ment.get(("p_a", "p_b"), {}).get("count") == 2)
    check("names that are everyday words don't invent links", not any("p_c" in k for k in ment))
    check("a first name two contacts share only counts as the full name",
          ment.get(("p_b", "p_d"), {}).get("count") == 2 and not any("p_e" in k for k in ment))
    net = enrich(connections(st.corpus, st.plans, st.now), st.corpus, st.plans, st.profiles)
    biggest = max((len(g["members"]) for g in net["interests"]), default=0)
    check("interest groups pick people out rather than listing everyone",
          0 < biggest <= len(st.corpus.contacts) // 2, f"largest group {biggest} of {len(st.corpus.contacts)}")
    linked = {tuple(sorted((a, x["id"]))) for a, lst in net["by_contact"].items() for x in lst}
    check("introductions are only suggested between people not already connected",
          net["introductions"] and all(tuple(sorted((i["a"]["id"], i["b"]["id"]))) not in linked
                                       for i in net["introductions"]),
          f"{len(net['introductions'])} suggestions")
    check("each introduction names what they have in common",
          all(i["shared"] and i["shared"][0] in i["draft"] for i in net["introductions"]))
    intro_copy = " ".join(i["why"] + " " + i["draft"] for i in net["introductions"]).lower()
    check("introductions never read as matchmaking",
          not any(w in intro_copy for w in ("romantic", "couple", "chemistry", "soulmate", "hit it off", " date")))
    attacker = {"origin": "http://attacker.example"}
    check("naming people and restoring threads refuse cross-site requests",
          client.post("/api/contacts/p_priya/name", json={"name": "x"}, headers=attacker).status_code == 403
          and client.post("/api/threads/t_p_priya/verdict", json={"real": False}, headers=attacker).status_code == 403)

    print("\npeople you only know through group chats:")
    gn = datetime(2026, 9, 1, 12)
    gc = {"p_me1": _Contact("p_me1", "Ana Park", ""), "p_g": _Contact("p_g", "Theo Lin", "")}
    gt = {"t_p_me1": _Thread("t_p_me1", "Ana Park", ("p_me1",), False),
          "g_1": _Thread("g_1", "Book Club", ("p_me1", "p_g"), True)}
    gm = []
    for i in range(60):
        ts = gn - timedelta(days=60 - i)
        gm.append(_Message(f"d{i}", "t_p_me1", _SELF if i % 2 else "p_me1", ts, "how was your day", bool(i % 2)))
        gm.append(_Message(f"g{i}", "g_1", ["p_me1", "p_g", _SELF][i % 3], ts + timedelta(hours=1),
                           "see everyone thursday", i % 3 == 2))
    gprofiles = {p.contact_id: p for p in profile_all(_Corpus(gc, gt, gm, "t"), gn)}
    theo = gprofiles.get("p_g")
    check("someone you only share a group chat with isn't described as gone quiet",
          theo is not None and theo.state == "group" and theo.trend == "none",
          f"Theo → {theo.state if theo else 'no profile'}")
    check("their summary says how you know them, not that anything faded",
          theo is not None and "Book Club" in theo.headline
          and not any(w in theo.headline.lower() for w in ("quiet", "faded", "used to")))
    check("the one-on-one friend in the same group still gets a real profile",
          gprofiles["p_me1"].state not in ("group", "new"), f"Ana → {gprofiles['p_me1'].state}")

    print("\nwhere people live:")
    from app.analysis.places import infer_locations
    from app.overrides import Overrides, clean_place
    ln = datetime(2026, 9, 1)
    lnames = {"p_a": "Priya", "p_b": "Marcus", "p_c": "Dana", "p_d": "Leo", "p_e": "Iris", "p_f": "Theo"}
    lc = {k: _Contact(k, v, "") for k, v in lnames.items()}
    lt = {f"t_{k}": _Thread(f"t_{k}", v, (k,), False) for k, v in lnames.items()}
    lsaid = [("p_a", 30, "ugh I live in Berkeley still"), ("p_a", 5, "ok we finally moved to Oakland!!"),
             ("p_b", 9, "here in brooklyn it's pouring"),
             ("p_c", 4, "flying to Denver this weekend!"), ("p_c", 3, "moved to a new team at work"),
             ("p_e", 7, "back home in India for the month"), ("p_e", 2, "here in india the food is unreal")]
    lm = [_Message(f"l{i}", f"t_{cid}", cid, ln - timedelta(days=d), text, False)
          for i, (cid, d, text) in enumerate(lsaid)]
    lm += [_Message("me1", "t_p_d", _SELF, ln, "how's life in Seattle treating you?", True),
           _Message("me2", "t_p_f", _SELF, ln, "I moved to Austin last year", True)]
    lm.sort(key=lambda m: m.ts)
    lcorpus = _Corpus(lc, lt, lm, "t")
    got = infer_locations(lcorpus)
    where = lambda cid: (got.get(cid) or {}).get("place")
    check("the newest statement of where someone lives wins", where("p_a") == "Oakland", str(where("p_a")))
    check("a passing 'here in' is kept only as a likely guess",
          where("p_b") == "Brooklyn" and got["p_b"]["confidence"] == "likely")
    check("trips and 'moved to a new team' never become a location", where("p_c") is None)
    check("asking 'how's life in Seattle?' suggests where they are", where("p_d") == "Seattle")
    check("your own move isn't pinned on the person you told", where("p_f") is None)
    check("an inferred location keeps the quote that suggested it", "Oakland" in got["p_a"]["quote"])
    try:
        clean_place("   ")
        blank_place = False
    except ValueError:
        blank_place = True
    check("a blank place is refused", blank_place)
    check("a place typed differently is saved as the same place",
          clean_place("  oakland ") == clean_place("OAKLAND") == clean_place("Oakland") == "Oakland"
          and clean_place("nyc") == "New York", clean_place("  oakland "))
    check("an unrecognised place typed in lowercase is tidied, not rewritten",
          clean_place("walnut creek") == "Walnut Creek" and clean_place("McKinney") == "McKinney")
    oak = TestClient(fastapi_app, base_url="http://127.0.0.1:8077")
    oak.cookies.set(COOKIE, guard.session)
    same_origin = {"origin": "http://127.0.0.1:8077"}
    oak.post("/api/contacts/p_priya/location", json={"place": "Oakland"}, headers=same_origin)
    oak.post("/api/contacts/p_nadia/location", json={"place": "  oakland "}, headers=same_origin)
    oak_groups = [g for g in oak.get("/api/connections").json()["places"] if g["place"] == "Oakland"]
    check("people saved in the same place are grouped together",
          len(oak_groups) == 1 and {m["id"] for m in oak_groups[0]["members"]} >= {"p_priya", "p_nadia"})
    for cid in ("p_priya", "p_nadia"):
        oak.post(f"/api/contacts/{cid}/location", json={"place": ""}, headers=same_origin)
    lroot = Path(tempfile.mkdtemp(prefix="mg-loc-")) / "vault"
    lkeys = MemoryKeyStore()
    lvault = Vault(lroot, lkeys)
    lvault.write(lcorpus, {"messages": len(lm)})
    lstore = OverrideStore(lvault)
    lo = lstore.load()
    lo.locations["p_c"] = "Denver"
    lo.location_unknown.add("p_f")
    lstore.save(lo)
    lback = OverrideStore(Vault(lroot, lkeys)).load()
    check("locations you give are saved encrypted and survive a restart",
          b"Denver" not in (lroot / "overrides.v1.enc").read_bytes()
          and lback.locations.get("p_c") == "Denver" and "p_f" in lback.location_unknown)
    check("corrections saved before locations existed still load",
          Overrides.from_dict({"names": {"x": "Sam"}}).locations == {})
    shutil.rmtree(lroot.parent, ignore_errors=True)
    import os as _os
    tmp3 = tempfile.mkdtemp(prefix="mg-locfx-")
    w3, cp3, ab3 = build(tmp3)
    w3.close()
    _os.environ["MG_AB_ROOT"] = ab3
    fxs = AppState(f"chatdb:{cp3}")
    first_ids = {c.name.split()[0]: c.id for c in fxs.corpus.contacts.values() if c.name[0].isalpha()}
    check("real message text produces a location guess",
          fxs.locations.get(first_ids["Priya"], {}).get("place") == "Oakland")
    ov3 = fxs.overrides.load()
    ov3.location_unknown.add(first_ids["Marcus"])
    fxs.overrides.save(ov3)
    fxs.recompute()
    check("saying you don't know also hides the app's guess", first_ids["Marcus"] not in fxs.locations)
    shutil.rmtree(tmp3, ignore_errors=True)
    check("setting a location refuses cross-site requests",
          client.post("/api/contacts/p_priya/location", json={"place": "Oakland"},
                      headers=attacker).status_code == 403)

    print("\nthe messages behind a plan:")
    stale_plan = next(p for p in app_state.plans if p.state == "stale")
    detail = client.get(f"/api/plans/{stale_plan.id}").json()
    shown = [m for g in detail["mentions"] for m in g["messages"]]
    in_thread = {m.id for m in app_state.corpus.messages_in(stale_plan.thread_id)}
    check("opening a plan shows the messages it came from, from its own conversation",
          len(shown) >= 2 and all(m["id"] in in_thread for m in shown), f"{len(shown)} messages")
    check("the message that named the plan is marked",
          any(m["names_it"] and detail["activity"].lower() in m["text"].lower() for m in shown))
    check("messages read oldest first", all(a["ts"] <= b["ts"] for a, b in zip(shown, shown[1:])))

    def _spans(p):
        # A plan merged from separate conversations has a gap of more than six hours
        # *between consecutive* messages -- a long, slow conversation doesn't count.
        ts = {m.id: m.ts for m in app_state.corpus.messages_in(p.thread_id)}
        got_ts = sorted(ts[i] for i in p.evidence if i in ts)
        return any(b - a > timedelta(hours=6) for a, b in zip(got_ts, got_ts[1:]))

    merged = next((p for p in app_state.plans if _spans(p)), None)
    merged_detail = client.get(f"/api/plans/{merged.id}").json() if merged else {"mentions": []}
    check("a plan that came up more than once shows each time separately",
          len(merged_detail["mentions"]) >= 2, f"{len(merged_detail['mentions'])} mentions")
    gone = client.get("/api/plans/plan9999")
    check("a plan that no longer exists says so", gone.status_code == 404 and "Reload" in gone.json().get("detail", ""))
    check("the plans overview still resolves alongside plan detail",
          client.get("/api/plans/overview").status_code == 200 and "counts" in client.get("/api/plans/overview").json())

    print("\nsaying thanks:")
    from collections import Counter as _Counter
    from app.analysis.gratitude import ELIGIBLE, build_gratitude
    gnow = datetime(2026, 9, 16, 12)

    def _gprof(cid, name):
        return _RP(contact_id=cid, name=name, msgs_per_week=5, msgs_per_week_recent=5, trend="steady",
                   warmth=70, importance=70, reciprocity=.5, initiation_balance=.5, my_median_reply_s=600,
                   their_median_reply_s=600, self_editing=33, depth=40, balance=40, state="close")

    def _gbase(cid, back, n=24):
        return [_Message(f"{cid}b{i}", f"t_{cid}", _SELF if i % 2 else cid,
                         gnow - timedelta(days=back - i * (back // n)),
                         "Hey how's it going!" if i % 2 else "good, you?", bool(i % 2)) for i in range(n)]

    gc = {"p_old": _Contact("p_old", "Olive Park", ""), "p_sam": _Contact("p_sam", "Sam Lee", ""),
          "p_ann": _Contact("p_ann", "Ana Ruiz", ""), "p_cut": _Contact("p_cut", "Cal Wu", "")}
    gt = {f"t_{k}": _Thread(f"t_{k}", v.name, (k,), False) for k, v in gc.items()}
    gm = _gbase("p_old", 500) + _gbase("p_sam", 200) + _gbase("p_ann", 367) + _gbase("p_cut", 498)
    gm += [_Message("s1", "t_p_sam", _SELF, gnow - timedelta(days=62),
                    "thank you so much for helping me move!! couldn't have done it without you", True),
           _Message("s2", "t_p_sam", _SELF, gnow - timedelta(days=30), "thanks for the info", True),
           _Message("s3", "t_p_sam", _SELF, gnow - timedelta(days=25), "thanks for covering rent this month", True),
           _Message("s4", "t_p_sam", "p_sam", gnow - timedelta(days=40), "thanks for the ride to the airport", False)]
    gm.sort(key=lambda m: m.ts)
    gd = {d.who: d for d in build_gratitude(_Corpus(gc, gt, gm, "t"),
                                            [_gprof(k, v.name) for k, v in gc.items()], [], gnow)}
    sam_d = gd.get("Sam Lee")
    check("a favour you thanked someone for becomes a thank-you",
          sam_d is not None and sam_d.kind == "thanked" and "helping me move" in sam_d.draft,
          sam_d.draft if sam_d else "none")
    check("small thanks, money, and their thanks to you are never used",
          sam_d is not None and not any(w in sam_d.draft.lower() for w in ("info", "rent", "airport")))
    check("the app describes the favour to you in your terms",
          sam_d is not None and "helping you move" in sam_d.why, sam_d.why if sam_d else "")
    check("a real texting anniversary is noticed", gd.get("Ana Ruiz") and gd["Ana Ruiz"].kind == "anniversary")
    check("the start of your imported history isn't mistaken for an anniversary",
          gd.get("Cal Wu") is None or gd["Cal Wu"].kind != "anniversary")
    demo_thanks = build_gratitude(app_state.corpus, app_state.profiles, app_state.plans, app_state.now)
    states = {p.contact_id: p.state for p in app_state.profiles}
    check("thank-yous only go to people you actually have a thread with",
          demo_thanks and all(states[d.target_ids[0]] in ELIGIBLE for d in demo_thanks),
          f"{len(demo_thanks)} thank-yous")
    check("one thank-you per person", len({d.who for d in demo_thanks}) == len(demo_thanks))
    wording = _Counter(d.draft.split(" ", 2)[2] if d.draft.count(" ") > 2 else d.draft for d in demo_thanks)
    openers = _Counter(" ".join(d.draft.lower().split()[2:4]) for d in demo_thanks)
    check("a list of thank-yous doesn't repeat one template", max(openers.values()) <= 2, str(dict(openers)))
    first_t = demo_thanks[0]
    again_t = next(d for d in build_gratitude(app_state.corpus, app_state.profiles, app_state.plans,
                                              app_state.now, {first_t.id: 1}) if d.id == first_t.id)
    check("'another version' changes a thank-you", again_t.draft != first_t.draft)
    one = client.get("/api/people/p_priya/gratitude").json()
    check("a single person's thank-you comes back, or a reason why not",
          (one["draft"] is not None) != (one["reason"] is not None)
          and client.get("/api/people/p_nobody/gratitude").status_code == 404)

    print("\nprivacy enforcement:")
    from app.privacy import ConsentError, NetworkGate, PrivacySettings, apply_settings, redact
    s = PrivacySettings()
    gate = NetworkGate(s)
    try:
        gate.authorize("draft", "hello")
        check("the gate blocks by default", False)
    except ConsentError:
        check("the gate blocks by default", True)
    check("blocked attempts are audited", len(gate.audit) == 1 and not gate.audit[0].allowed)
    gate.grant("draft")
    check("redaction applies to what is released",
          gate.authorize("draft", "call 415-555-0134") == "call [phone]")
    try:
        gate.authorize("tone", "x")
        check("consent is per-purpose", False)
    except ConsentError:
        check("consent is per-purpose", True)

    s2 = PrivacySettings(excluded_contact_ids={"p_priya"})
    filtered = apply_settings(corpus, s2)
    leaked = [t for t in filtered.threads.values() if "p_priya" in t.participant_ids]
    check("an excluded contact is gone from group threads too", not leaked)
    check("excluding someone actually removes their messages",
          len(filtered.messages) < len(corpus.messages),
          f"{len(corpus.messages)} → {len(filtered.messages)}")

    print()
    if FAILS:
        print(f"{len(FAILS)} FAILED: {', '.join(FAILS)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
