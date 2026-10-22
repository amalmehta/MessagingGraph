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
import os
import re
import shutil
import socket
import sqlite3
import sys
import subprocess
import tempfile
import unicodedata
import time
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


def _raises(exc, fn) -> bool:
    """True when `fn` fails the way it is supposed to."""
    try:
        fn()
    except exc:
        return True
    except Exception:
        return False
    return False


# ---------------------------------------------------------------------------
# Colour. The UI ships two themes over one set of token names, and every colour
# in the app -- including the ones that end up as SVG attributes on the charts
# and the force graph -- is read from those tokens at run time. These let the
# palette be checked rather than eyeballed: text has to stay legible on every
# surface it can land on, and the two chart series have to stay apart for a
# reader with any of the three kinds of colour blindness.
# ---------------------------------------------------------------------------
def _srgb(h: str) -> tuple[float, float, float]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def contrast_ratio(fg: str, bg: str) -> float:
    """WCAG 2.1 contrast. 4.5 is the AA floor for body text, 3.0 for graphics."""
    def lum(h):
        r, g, b = (_linear(c) for c in _srgb(h))
        return 0.2126 * r + 0.7152 * g + 0.0722 * b
    a, b = lum(fg), lum(bg)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


# Machado/Oliveira/Fernandes severity-1.0 matrices, the usual ones for this.
_CVD = {
    "protanopia":   ((0.152286, 1.052583, -0.204868), (0.114503, 0.786281, 0.099216),
                     (-0.003882, -0.048116, 1.051998)),
    "deuteranopia": ((0.367322, 0.860646, -0.227968), (0.280085, 0.672501, 0.047413),
                     (-0.011820, 0.042940, 0.968881)),
    "tritanopia":   ((1.255528, -0.076749, -0.178779), (-0.078411, 0.930809, 0.147602),
                     (0.004733, 0.691367, 0.303900)),
}


def _simulate(h: str, kind: str) -> str:
    r, g, b = (_linear(c) for c in _srgb(h))
    m = _CVD[kind]
    out = []
    for i in range(3):
        v = max(0.0, min(1.0, m[i][0] * r + m[i][1] * g + m[i][2] * b))
        v = 12.92 * v if v <= 0.0031308 else 1.055 * (v ** (1 / 2.4)) - 0.055
        out.append(round(max(0.0, min(1.0, v)) * 255))
    return "#%02x%02x%02x" % tuple(out)


def _lab(h: str) -> tuple[float, float, float]:
    r, g, b = (_linear(c) for c in _srgb(h))
    X = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    Y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    Z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883
    f = lambda t: t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116
    fx, fy, fz = f(X), f(Y), f(Z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def colour_distance(a: str, b: str) -> float:
    """Smallest CIE76 ΔE between two colours across normal vision and all three
    kinds of colour blindness. CIE76 rather than CIEDE2000: cruder, but these two
    colours are nowhere near each other and a rough floor is enough to catch a
    palette edit that accidentally puts them side by side."""
    pairs = [(a, b)] + [(_simulate(a, k), _simulate(b, k)) for k in _CVD]
    return min(math.dist(_lab(x), _lab(y)) for x, y in pairs)


def theme_tokens(css: str) -> dict[str, dict[str, str]]:
    """The hex-valued custom properties of each theme block in styles.css."""
    themes = {}
    for name, pattern in (("light", r":root\{(.*?)\}"),
                          ("dark", r':root\[data-theme="dark"\]\{(.*?)\}')):
        m = re.search(pattern, css, re.S)
        if m:
            themes[name] = dict(re.findall(r"--([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})", m.group(1)))
    return themes


def corr(name: str, xs: list[float], ys: list[float], floor: float) -> None:
    r = correlation(xs, ys)
    check(f"{name} correlates with ground truth (r >= {floor})", r >= floor, f"r = {r:.2f}")


class _Plain2:
    """Only the fields the phrasing helpers read."""
    lowercase = False
    greeting = ""


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

    # "done" covers two different claims: someone said it was happening and the day
    # came, or nobody said anything and the day came anyway. Only the first is known.
    done = [p for p in plans if p.state == "done"]
    check("a plan is only assumed when nothing said it happened",
          all(not p.assumed for p in plans if p.state != "done") and any(p.assumed for p in done),
          f"{sum(p.assumed for p in done)} of {len(done)} done plans are inferred from the date")

    from app.analysis.plans import _clock as _ck
    check("a time of day is read the way it was written",
          [_ck(s) for s in ("7pm", "9am", "7:30pm", "noon", "midnight", "13:45")]
          == [(19, 0), (9, 0), (19, 30), (12, 0), (0, 0), (13, 45)])
    check("a bare clock time on a plan is read as the evening", _ck("6:30") == (18, 30))
    check("nonsense is not a time", _ck("25:00") is None and _ck("7:99") is None)

    # A day can be named in order to rule it out. Taking the first day in the message
    # put the plan on the day that was refused. No message in the demo corpus names
    # two days, so this is the only thing exercising it.
    from app.analysis.plans import _wanted_day as _wd
    def _day(s):
        m = _wd(s)
        return m.group(2).lower() if m else None
    check("a day that was turned down doesn't become the plan",
          _day("not friday, how about saturday") == "saturday"
          and _day("friday doesn't work, saturday?") == "saturday"
          and _day("can't do monday but tuesday works") == "tuesday"
          and _day("tuesday is no good, thursday then") == "thursday")
    check("one refusal covers the whole list it heads",
          _day("can't do friday or saturday") == "friday"
          and _day("can't do friday, saturday or sunday") == "friday")
    from app.analysis.plans import _resolve_date as _rd
    _anchor = datetime(2026, 3, 4, 12)          # a Wednesday
    check("a greeting is not a date",
          _wd("hey, how's your week") is None and _wd("let's see how the week goes") is None)
    check("a week is kept as timing but never turned into a day",
          _rd("let's do dinner this week", _anchor) == ("this week", None)
          and _rd("drinks next week?", _anchor) == ("next week", None))
    check("a weekend still lands on the weekend",
          (_rd("brunch this weekend", _anchor)[1] or _anchor).weekday() == 5)
    check("a day nobody argued with is still the first one named",
          _day("saturday or sunday?") == "saturday"
          and _day("dinner friday the 14th") == "friday"
          and _day("monday? or tuesday if easier") == "monday"
          and _day("how about this weekend") == "weekend")
    # The date used to be stored at 7pm whatever the message said, so "tuesday noon"
    # and "next weekend 9am" both came out as 19:00.
    _T = re.compile(r"\b(?:(\d{1,2})(?::(\d{2}))?\s*(am|pm)|noon|midnight)\b", re.I)
    off = []
    for pl in plans:
        if not (pl.when_date and pl.when_text):
            continue
        m = _T.search(pl.when_text)
        if not m:
            continue
        if m.group(0).lower() == "noon":
            want = (12, 0)
        elif m.group(0).lower() == "midnight":
            want = (0, 0)
        else:
            want = (int(m.group(1)) % 12 + (12 if m.group(3).lower() == "pm" else 0),
                    int(m.group(2) or 0))
        if (pl.when_date.hour, pl.when_date.minute) != want:
            off.append(pl.when_text)
    check("a plan's date keeps the time the message named", not off,
          f"{len(off)} disagree" if off else "every dated plan that names a time agrees")
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
    from app.analysis.voice import style_for as _style_for
    rafa_style, group_voice = _style_for(st.corpus, st.now, "p_rafa"), _style_for(st.corpus, st.now)
    greets_ok = ((not rafa_style.greeting or "rafa" in re.split(r"[,.!?\n]", solo_p.draft.lower())[0])
                 and (not group_voice.greeting or ("you two" in pair_p.draft.lower()
                                                   and f"{group_voice.greeting} all" in props[0].draft.lower())))
    check("drafts greet one person, two people and a group correctly", greets_ok, props[0].draft.split("\n")[0][:40])

    print("\nmessages worth sending:")
    from app.analysis.messages import build_messages
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
    # A plan draft's seed picks the activity and the date, not only the phrasing, so
    # cycling it has to offer a different thing to do -- not reword the same dinner.
    plan_draft = next((d for d in drafts if d.kind in ("revive_plan", "group_plan")), None)
    check("a plan draft is among the suggestions", plan_draft is not None)
    if plan_draft:
        versions = [v for s in range(plan_draft.options)
                    for v in build_messages(st.corpus, st.profiles, st.plans, st.now,
                                            {plan_draft.id: s}) if v.id == plan_draft.id]
        texts, dates = {v.draft for v in versions}, {v.detail for v in versions}
        check("'another version' on a plan offers another thing to do, not just new wording",
              len(texts) > 3 and len(dates) > 1,
              f"{len(texts)} drafts and {len(dates)} dates across {plan_draft.options} options")
    check("every version the button offers is a different message",
          plan_draft is not None and len(texts) == plan_draft.options,
          f"{len(texts)} distinct of {plan_draft.options}" if plan_draft else "no plan draft")
    if plan_draft:
        # Every version has to say what you're proposing. "it's been a minute / want to go
        # friday at 7" doesn't, and it used to be what every activity collapsed into.
        from app.analysis.proposals import propose as _propose
        unnamed = []
        for s in range(plan_draft.options):
            prop_s = _propose(st.corpus, st.plans, [plan_draft.target_ids[0]],
                              plan_draft.thread_id, s, st.now)
            draft_s = versions[s].draft.lower()
            if not any(w in draft_s for w in prop_s.activity.lower().split() if len(w) > 3):
                unnamed.append((s, prop_s.activity))
        check("every version says what you're suggesting", not unnamed,
              f"{len(unnamed)} of {plan_draft.options} don't name it" if unnamed
              else f"all {plan_draft.options} name their activity")
    from app.analysis.voice import plan_parts as _plan_parts, style_for as _vstyle, write as _vwrite
    vnow = datetime(2026, 9, 16, 12)

    def _vcorpus(lines, burst=False, old=()):
        vc = {"p_x": _Contact("p_x", "muds", "")}
        vt = {"t_x": _Thread("t_x", "muds", ("p_x",), False)}
        vm, t0v = [], vnow - timedelta(days=9)
        for i, text in enumerate(lines):
            vm.append(_Message(f"r{i}", "t_x", _SELF, t0v + timedelta(hours=7 * i), text, True))
            if burst and i % 2 == 0:
                vm.append(_Message(f"b{i}", "t_x", _SELF, t0v + timedelta(hours=7 * i, seconds=40), "like actually", True))
            vm.append(_Message(f"s{i}", "t_x", "p_x", t0v + timedelta(hours=7 * i, minutes=3), "ok", False))
        vm += [_Message(f"o{i}", "t_x", _SELF, vnow - timedelta(days=60, hours=i), text, True) for i, text in enumerate(old)]
        vm.sort(key=lambda m: m.ts)
        return _Corpus(vc, vt, vm, "t")

    casual = ["yo wanna grab food sat", "im down", "u free tmrw", "lol thats wild", "dont worry about it", "yo u around",
              "wanna hit the gym at 7", "lmk", "u good", "haha yes", "cant tonight", "sat works", "yo whats up", "lol",
              "wanna do sat at 7", "u coming", "idk yet lol", "im so tired", "whats the plan", "yo", "sounds good",
              "down", "u around sat", "lmk when u free", "yo wanna get dinner at 8"]
    careful = ["Hey! Hope your week is going well.", "Would you want to get dinner on Saturday at 7pm?",
               "That sounds great.", "I'm running a bit late, sorry.", "Hi, are you free tomorrow?", "Let's do Friday.",
               "Thanks so much, that's really kind.", "Hey, what time works for you?", "I can't make it tonight.",
               "Happy to help.", "Hey! Did you see the news?", "Want to go for a walk on Sunday?", "That's perfect.",
               "Hi there, how was the trip?", "I'll bring snacks.", "Hey, you around this weekend?", "Sounds good to me.",
               "Let's catch up soon.", "I'm free after 6pm.", "Hey! Congrats on the new job.", "What's the address?",
               "Thank you for dinner.", "Hey, let me know when you land.", "I'll call you later.", "Hope you're well."]
    cs = _vstyle(_vcorpus(casual, burst=True), vnow, "p_x")
    ks = _vstyle(_vcorpus(careful, old=["GOOD MORNING!!"] * 60), vnow, "p_x")
    sat = vnow + timedelta(days=3)
    c0, c1 = (_vwrite(_plan_parts(cs, "dinner", sat, vnow, 19, "muds", False, True, s), cs) for s in (0, 1))
    k0, k1 = (_vwrite(_plan_parts(ks, "dinner", sat, vnow, 19, "muds", False, True, s), ks) for s in (0, 1))
    check("a casual texter's drafts sound casual",
          c1 == c1.lower() and "wanna" in c1 and "sat" in c1 and "?" not in c1, repr(c1))
    check("a careful texter's drafts sound careful",
          k1.startswith("Hey muds") and "Saturday" in k1 and "7pm" in k1, repr(k1))
    check("style comes from your last 10 days, not old habits",
          ks.window_days == 10 and not ks.lowercase and "GOOD" not in k0)
    check("separate short lines only for people who text in bursts", "\n" in c0 and "\n" not in k0)
    check("a greeting never runs into a capitalised sentence", not re.search(r", (?!I\b)[A-Z]", k0 + k1), repr(k0))
    check("an activity isn't named twice in one draft", c0.count("dinner") == 1 and k0.count("dinner") == 1)
    from app.analysis.voice import Style as _VStyle, reconnect_parts as _reconnect_parts
    plain = _VStyle(10, 30, lowercase=True, greeting=None, periods=False, question_marks=False, bursts=False,
                    emoji="😂", warm_emoji=None)
    sincere = _vwrite(_reconnect_parts(plain, "muds", 0), plain, warm=True)
    check("sincere drafts never get a laughing emoji", "😂" not in sincere, repr(sincere))
    check("unpunctuated sentences aren't run together", "\n" in sincere, repr(sincere))
    from app.analysis.voice import thanks_parts as _thanks_parts
    ushort = _VStyle(10, 30, shortforms={"u", "ur"}, apostrophes=True)
    keeps, shortens = (_vwrite(_thanks_parts(ushort, "muds", "appreciation", s), ushort) for s in (0, 1))
    check('"u" shortens "you" but never a contraction',           # "u're" would be a substring of "you're"
          "you're" in keeps and not re.search(r"\bu['\u2019]", keeps), repr(keeps))
    check('"u" still replaces a plain "you"', shortens.endswith(" u"), repr(shortens))
    trip_c = _vwrite(_plan_parts(cs, "trip", sat, vnow, 19, "bruds", False, True, 0), cs)
    check("trips get planned, not booked for 7pm", "plan" in trip_c and "7" not in trip_c, repr(trip_c))

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
          bool(numbered) and numbered[0].draft.lower().startswith("hey")
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

    print("\nletting a phone in:")
    from app.pairing import Pairing as _P, PairingStore as _PS, PairingStore, MAX_ATTEMPTS as _MAX
    _pg = _P()
    check("pairing is closed until you open it",
          not _pg.offering and _pg.code_on_offer() is None)
    _code = _pg.open()
    _wrong = "000000" if _code != "000000" else "111111"
    check("the code is six digits and runs out", len(_code) == 6 and _code.isdigit()
          and 0 < _pg.seconds_left() <= 120, f"{_pg.seconds_left()}s left")
    check("a wrong code pairs nothing and doesn't spend the offer",
          _pg.redeem(_wrong, "iPhone") is None and _pg.offering)
    _dev = _pg.redeem(_code, "An iPhone")
    check("the right code pairs the phone once", _dev is not None and bool(_dev.token))
    check("and the code is spent", not _pg.offering and _pg.redeem(_code, "again") is None)
    check("the token identifies that phone and nothing else",
          _pg.device_for(_dev.token) is _dev
          and _pg.device_for("nope") is None and _pg.device_for(None) is None)
    # The UI lists paired phones; a token in that list would be a token on screen.
    check("a device never shows its own token", "token" not in _dev.public())
    _bf = _P(); _real = _bf.open()
    for _ in range(_MAX + 1):
        _bf.redeem("999999" if _real != "999999" else "888888", "x")
    check("guessing at six digits closes pairing rather than letting you keep going",
          not _bf.offering, f"shut after {_MAX} wrong")
    check("and the Mac is told it was guessing that shut it, not you",
          _bf.shut_by_guessing and not _P().shut_by_guessing)
    _ex = _P(); _ex.open(); _ex._code_until = 0.0
    check("an expired code is no code at all", not _ex.offering)
    _store = _PS(); _pg2 = _store.load(); _c2 = _pg2.open()
    _d2 = _pg2.redeem(_c2, "Phone"); _store.save(_pg2)
    _back = _store.load()
    check("a paired phone survives a restart", _back.device_for(_d2.token) is not None)
    check("and can be revoked one phone at a time",
          _back.revoke(_d2.id) and _back.device_for(_d2.token) is None)

    # Against a real Vault, not just the in-memory store. The first version of
    # this called Vault.read/write -- which are the corpus, not a named document
    # -- so the server died on startup for anyone who had actually imported, and
    # every check above passed anyway because none of them had a vault.
    _pvroot = Path(tempfile.mkdtemp(prefix="mg-pair-")) / "vault"
    _pvault = Vault(_pvroot, MemoryKeyStore())
    _pvault.write(corpus, {"messages": len(corpus.messages)})
    _ps3 = PairingStore(_pvault)
    _pg3 = _ps3.load()
    check("a vault with no phones in it yet reads as no phones", not _pg3.devices)
    _c3 = _pg3.open()
    _d3 = _pg3.redeem(_c3, "Sealed phone")
    _ps3.save(_pg3)
    _reopened = PairingStore(_pvault).load()
    check("a paired phone is sealed into the vault and comes back after a restart",
          _reopened.device_for(_d3.token) is not None)
    check("and it is on disk encrypted, not as readable JSON",
          b"Sealed phone" not in (_pvroot / PairingStore.NAME).read_bytes())
    shutil.rmtree(_pvroot.parent, ignore_errors=True)

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

    print("\nletting the phone in over the network:")
    MAC_ORIGIN = {"origin": "https://127.0.0.1:8077"}
    STATIC = Path("app/static")
    # A second, separate app object built the way `./mg serve --phone` builds it,
    # so the loopback one the checks above used stays exactly as locked as it was.
    # Loaded under its own name rather than reloaded: importlib.reload mutates the
    # module in place, which would hand both halves of this file the same app.
    import importlib.util
    _env = dict(os.environ)
    os.environ.update(MG_SOURCE="synthetic", MG_PORT="8077", MG_PHONE="1")
    _spec = importlib.util.spec_from_file_location("app.main_on_lan", "app/main.py")
    lanmod = importlib.util.module_from_spec(_spec)
    sys.modules["app.main_on_lan"] = lanmod
    try:
        _spec.loader.exec_module(lanmod)
    finally:
        os.environ.clear(); os.environ.update(_env)

    LAN = lanmod.LAN_HOSTS[0] if lanmod.LAN_HOSTS else "nowhere.local:8077"
    check("--phone publishes a .local name and not a loopback one",
          bool(lanmod.LAN_HOSTS) and not any(h.startswith("127.") or h.startswith("localhost")
                                             for h in lanmod.LAN_HOSTS), LAN)
    lanmac = TestClient(lanmod.app, base_url="https://127.0.0.1:8077")
    lanmac.get(f"/?token={lanmod.guard.token}")
    ph = TestClient(lanmod.app, base_url=f"https://{LAN}")
    ORIG = {"origin": f"https://{LAN}".lower()}

    check("an unpaired phone on the Wi-Fi gets nothing but the pairing page",
          ph.get("/api/overview").status_code == 401 and ph.get("/").status_code == 401
          and ph.get("/pair").status_code == 200)
    check("a host that isn't this Mac is still refused",
          ph.get("/", headers={"host": "attacker.example:8077"}).status_code == 403)
    _code = lanmac.post("/api/phone/open", headers=MAC_ORIGIN).json()["code"]
    # 401 here, not 403: this phone hasn't paired yet, so it is turned away before
    # anyone asks whether it would be allowed to see a code. Once it has paired it
    # is refused on the other ground, which the paired check below covers.
    check("the code is shown on the Mac and never over the network",
          len(_code) == 6 and ph.get("/api/phone").status_code == 401)
    _bad = ph.post("/api/pair/redeem", headers=ORIG,
                   json={"code": "000001" if _code != "000001" else "000002"})
    check("a wrong code hands out no cookie",
          _bad.status_code == 401 and "mg_device" not in _bad.cookies)
    _ok = ph.post("/api/pair/redeem", json={"code": _code, "name": "A phone"}, headers=ORIG)
    _sc = _ok.headers.get("set-cookie", "").lower()
    check("the right code hands out an HttpOnly, SameSite=Strict device cookie",
          _ok.status_code == 200 and "httponly" in _sc and "samesite=strict" in _sc)
    check("and a Secure one, because the phone is only ever served over https",
          "secure" in _sc)
    check("a page this server never serves, plain http, is refused as cross-origin",
          ph.post("/api/pair/redeem", json={"code": "000000"},
                  headers={"origin": f"http://{LAN}".lower()}).status_code == 403)
    check("the Mac is shown https addresses for the phone",
          all(a.startswith("https://") for a in lanmac.get("/api/phone").json()["addresses"]))
    check("and then the phone can read the same app the Mac sees",
          ph.get("/api/overview").status_code == 200)
    check("a paired phone still can't pair the next one",
          ph.post("/api/phone/open", headers=ORIG).status_code == 403
          and ph.get("/api/phone").status_code == 403)
    check("someone else on the Wi-Fi is no better off than before",
          TestClient(lanmod.app, base_url=f"https://{LAN}")
          .get("/api/overview").status_code == 401)
    _dev = lanmac.get("/api/phone").json()["devices"][0]
    check("the Mac lists the phone without ever showing its token", "token" not in _dev)
    lanmac.post(f"/api/phone/revoke/{_dev['id']}", headers=MAC_ORIGIN)
    check("revoking it shuts that phone out at once",
          ph.get("/api/overview").status_code == 401)

    print("\nadded to the Home Screen:")
    _idx = ph.get("/pair").status_code and lanmac.get("/").text
    check("the page declares itself installable, with an icon iOS can mask",
          'rel="manifest"' in _idx and 'apple-touch-icon' in _idx
          and 'name="apple-mobile-web-app-capable"' in _idx)
    _man = lanmac.get("/static/manifest.webmanifest")
    _mj = _man.json()
    check("the manifest is served as a manifest and opens standalone",
          _man.headers["content-type"] == "application/manifest+json"
          and _mj["display"] == "standalone" and _mj["start_url"] == "/")
    check("the CSP allows the manifest it asks for",
          "manifest-src 'self'" in lanmac.get("/").headers["content-security-policy"])
    _ico = lanmac.get("/static/icon-180.png")
    check("the home-screen icon is a real PNG, square and opaque",
          _ico.headers["content-type"] == "image/png"
          and _ico.content[:8] == b"\x89PNG\r\n\x1a\n"
          and int.from_bytes(_ico.content[16:20], "big") == 180
          and int.from_bytes(_ico.content[20:24], "big") == 180,
          f"{len(_ico.content):,} bytes")
    check("the phone layout reaches the stylesheet and keeps the tab bar clear of the home bar",
          "max-width:760px" in (STATIC / "styles.css").read_text()
          and "env(safe-area-inset-bottom)" in (STATIC / "styles.css").read_text())

    print("\na third sweep, and what it turned up:")
    from app.search import terms as _t3, MAX_TERM as _MT
    # "ski ski ski trip" was four terms of which two were distinct, so the
    # repeat outvoted the rare word it was sitting next to.
    check("a word typed twice is still one word",
          _t3("ski ski ski trip") == ["ski", "trip"])
    check("and a pasted paragraph does not become one enormous term",
          len(_t3("z" * 200)[0]) == _MT)
    _ajs3 = Path("app/static/app.js").read_text()
    _css3 = Path("app/static/styles.css").read_text()
    _html3 = Path("app/static/index.html").read_text()
    check("every chart says what it is, not just the one that did",
          'class="daybars" role="img"' in _ajs3 and 'class="topppl" role="list"' in _ajs3)
    check("a message you searched for opens the person it is with",
          "x.name === h.who" in _ajs3 and "openPerson(who.contact_id)" in _ajs3)
    check("the Insights tab carries its number like the others",
          'id="c-insights"' in _html3 and "$('#c-insights')" in _ajs3)
    # The drawer opened at h3 and ran h2s beneath it.
    _d = _ajs3[_ajs3.index("${avatar(p.name, 'big')}"):]
    _d = _d[:_d.index("Chat history") + 20]
    check("the drawer's title outranks the sections inside it",
          "<h2>${avatar(p.name, 'big')}" in _ajs3
          and "<h2>Connected to</h2>" not in _ajs3
          and "<h3>Connected to</h3>" in _ajs3)
    # Three rules each written twice, a few hundred lines apart.
    import re as _re3
    from collections import Counter as _C3
    _sels = [x.strip() for x in _re3.findall(r"^([.#][\w.#>\s:()-]+)\{", _css3, _re3.M)]
    check("no rule describes the same element in two places",
          not [x for x, c in _C3(_sels).items() if c > 1])
    check("one apology, not two spellings of it",
          "work that one out" not in _ajs3)
    check("and the months are written once",
          "MONTHS, when_words" in Path("app/analysis/gratitude.py").read_text()
          and 'MONTHS = ["January"' not in Path("app/analysis/gratitude.py").read_text())

    print("\nthe whole history at once:")
    from app.insights import build as _ins, DAYS as _IDAYS
    _i = _ins(app_state.corpus, app_state.profiles, app_state.now)
    check("it adds up to what is actually there",
          _i["messages"] == len(app_state.corpus.messages)
          and _i["mine"] + _i["theirs"] == _i["messages"],
          f"{_i['messages']:,} messages")
    check("shares are percentages of that, not of something else",
          0 <= _i["mine_share"] <= 100 and 0 <= _i["group_share"] <= 100
          and abs(_i["mine_share"] - round(100 * _i["mine"] / _i["messages"])) < 1)
    check("every hour and every weekday is accounted for",
          len(_i["by_hour"]) == 24 and len(_i["by_day"]) == 7
          and sum(_i["by_hour"]) == _i["messages"] == sum(_i["by_day"]))
    check("the busiest day it names is the busiest one there is",
          _i["busiest_day"] == _IDAYS[_i["by_day"].index(max(_i["by_day"]))])
    check("and the busiest hour agrees with the hours",
          _i["busiest_hour"] == _i["by_hour"].index(max(_i["by_hour"])))
    check("the people it lists are the ones you talk to most",
          [t["messages"] for t in _i["top"]] == sorted(
              [t["messages"] for t in _i["top"]], reverse=True))
    check("a day count is never zero, however short the history",
          _i["days"] >= 1 and _i["per_day"] > 0)
    # An empty corpus must not divide by anything.
    from dataclasses import replace as _rpl
    _empty = _rpl(app_state.corpus, messages=(), threads={}, contacts={})
    check("and nothing at all is said plainly rather than crashing",
          _ins(_empty, [], app_state.now)["empty"] is True)
    _ijs = Path("app/static/app.js").read_text()
    check("it has a page of its own, not a corner of People",
          "viewInsights" in _ijs and "insights: 'Insights'" in _ijs
          and 'data-view="insights"' in Path("app/static/index.html").read_text())
    check("and nothing on it is a score out of ten",
          "not marks out of ten" in _ijs)

    print("\nwhat WhatsApp actually hands you:")
    from app.ingest.whatsapp import (clean_name as _cn, _from_jid as _fj,
                                     NOT_A_CONVERSATION as _NAC, NOT_A_PERSON as _NAP,
                                     JID_ONLY as _JO, GROUP_EVENT as _GE)
    # Names arrive wrapped in direction marks and padded with non-breaking
    # spaces. Invisible, and they travel into every search and every avatar.
    check("invisible direction marks are taken out of names",
          _cn("\u202a+39\xa0333\xa0000\xa00000\u202c") == "+39 333 000 0000"
          and _cn("\u200eYou") == "You")
    check("and an identifier stored where a name should be is not a name",
          _cn("+CgAQvLL71QYgAw==") == "" and _cn("Maria De La Cruz") == "Maria De La Cruz")
    # Most of this history is @lid, which is not a phone number.
    check("a phone number is only read off a jid that holds one",
          _fj("15551234567@s.whatsapp.net") == "+15551234567"
          and _fj("179735841767521@lid") == "Unknown"
          and _fj("CgAQvLL71QYgAw==@lid") == "Unknown")
    check("status and the broadcast list are not conversations",
          "@status" in _NAC and "@broadcast" in _NAC)
    check("WhatsApp's own account is not a friend",
          "whatsapp" in _NAP and "you" in _NAP)
    # "179735841767521@lid" is someone joining a group, stored as a message.
    check("a group event is not something anyone said",
          bool(_JO.match("179735841767521@lid")) and _GE == 6
          and not _JO.match("see you at 7@the pub"))
    _wsrc = Path("app/ingest/whatsapp.py").read_text()
    check("people are named from what they call themselves",
          "ZWAPROFILEPUSHNAME" in _wsrc and "pushed.get(jid" in _wsrc)
    check("including the name carried on their messages",
          "ZPUSHNAME IS NOT NULL" in _wsrc and "pushed.setdefault" in _wsrc)
    # Run against the real store when it is here; skipped cleanly when not.
    from app.ingest.whatsapp import WhatsAppSource as _WA
    _w = _WA(days=400, id_key=b"a-key-for-this-check-only-32bytes")
    if _w.available()[0]:
        _wc = _w.load()
        _nm = [x.name for x in _wc.contacts.values()]
        check("and on this Mac nothing unreadable survives any of that",
              not [n for n in _nm if "==" in n or n.lower() in ("you", "whatsapp")
                   or any(unicodedata.category(ch) == "Cf" for ch in n)],
              f"{len(_nm)} people")
        check("with real names recovered rather than left as identifiers",
              sum(1 for n in _nm if not n.startswith("+") and n != "Unknown") > 10)
    else:
        check("and on this Mac nothing unreadable survives any of that",
              True, "no WhatsApp here to read")
        check("with real names recovered rather than left as identifiers",
              True, "no WhatsApp here to read")

    print("\nwarmer, without getting silly about it:")
    _fjs = Path("app/static/app.js").read_text()
    _fcss = Path("app/static/styles.css").read_text()
    check("a draft card shows a face, because these are people",
          "${avatar(d.who)}" in _fjs and ".dwho{" in _fcss)
    check("and so does an observation",
          '<span class="cwho">${avatar(a.who)}' in _fjs)
    # The message is what you came for; the reason is a note about it.
    _card = _fjs[_fjs.index("function draftCard"):]
    _card = _card[:_card.index("\n}")]
    check("the message comes before the reason for it",
          _card.index("dtext") < _card.index('class="why"'))
    check("and is the larger of the two",
          "font:15px/1.55" in _fcss)
    # Said in words, not shouted in mono capitals named after the code.
    check("what a card is for is said plainly",
          "reply: 'your turn'" in _fjs and "reconnect: 'say hi'" in _fjs
          and "revive_plan: 'pick a date'" in _fjs)
    check("and nothing is sold to you",
          not any(w in _fjs for w in ("make it happen", "get everyone out",
                                      "Let's go", "Crush", "🔥")))
    check("observations are labelled the same way",
          "NOTICE_KIND" in _fjs and "'worth a thought'" in _fjs)
    # "well over200% more than your usual" was on screen.
    from app.analysis.relationships import profile_all as _pa2
    _bad = [sg.display for q in app_state.profiles for sg in q.signals
            if sg.display and re.search(r"[a-z]\d", sg.display)]
    check("no sentence runs a word into a number", not _bad, str(_bad[:1]))
    check("the greeting sounds like a person, not a form",
          "'Morning'" in _fjs and "Good morning" not in _fjs)

    print("\nkeeping it current on its own:")
    import app.refresh as _rf
    _st = _rf.Status()
    _h = _rf.Schedule()
    _h.status = _rf.Status(enabled=True, last_run=None)
    # Named for the job, not the interval: a class called Hourly that runs
    # daily is a lie that outlives whoever changed the number.
    check("it refreshes once a day, and nothing claims otherwise",
          _rf.EVERY == timedelta(days=1)
          and not [f for f in ("app/refresh.py", "app/desktop.py", "app/static/app.js")
                   if "hourly" in Path(f).read_text().lower()],
          str(_rf.EVERY))
    check("and it checks often enough that a sleeping Mac catches up",
          _rf.CHECK_EVERY < _rf.EVERY / 24)
    check("a schedule that has never run is due", _h.due())
    _h.status.last_run = app_state.now
    check("and is not due again straight away", not _h.due(app_state.now))
    check("but is due again once the interval has passed",
          _h.due(app_state.now + _rf.EVERY + timedelta(seconds=1)))
    _h.status.enabled = False
    check("switched off, it is never due",
          not _h.due(app_state.now + timedelta(days=3)))
    # A timer is not permission to read something you never imported.
    _asked = []
    _real = _rf.refresh_once
    check("it refreshes only what you already imported",
          "vault.imported()" in Path("app/refresh.py").read_text())
    check("and an export you dragged in is not re-read behind your back",
          "nothing can refresh an export" in Path("app/refresh.py").read_text())
    # Half the job failing must not take the other half with it.
    check("one platform failing does not stop the others",
          "except Exception as e:" in Path("app/refresh.py").read_text()
          and "out.append(Outcome(p, False" in Path("app/refresh.py").read_text())
    _dt2 = Path("app/desktop.py").read_text()
    check("the schedule lives in the app, which is what holds the permission",
          "refreshing.Schedule" in _dt2 and "start_refreshing" in _dt2)
    check("it waits rather than reloading the page under your hands",
          "_window_in_use" in _dt2 and "pending_refresh" in _dt2)
    check("and applies what it held back when you next open the window",
          "self.pending_refresh = False" in _dt2
          and "applicationShouldHandleReopen" in _dt2)
    check("you can switch it off, and do it by hand",
          "toggleRefreshing:" in _dt2 and "refreshNow:" in _dt2)
    check("what it last managed is shown, including what it could not do",
          "/api/refresh" in Path("app/main.py").read_text()
          and "couldn't be read" in Path("app/static/app.js").read_text())
    _bad = _rf.load_status.__doc__ is None
    _st2 = _rf.Status(enabled=True, last_run=app_state.now,
                      last_results=[_rf.Outcome("whatsapp", True, 10)])
    # The thread itself, on a short clock, rather than only its arithmetic.
    import threading as _th, time as _tm
    _save, _first, _every, _check = (_rf.save_status, _rf.FIRST_RUN_DELAY,
                                     _rf.EVERY, _rf.CHECK_EVERY)
    try:
        _rf.save_status = lambda st: None          # never touch the real file
        _rf.FIRST_RUN_DELAY = timedelta(seconds=0.2)
        _rf.EVERY = timedelta(seconds=0.5)
        _rf.CHECK_EVERY = timedelta(seconds=0.2)
        _ran, _busy = [], {"on": True}
        _hh = _rf.Schedule(on_done=lambda r: _ran.append(r), should_defer=lambda: _busy["on"])
        _hh.status = _rf.Status(enabled=True, last_run=None)
        _hh.run_now = lambda: (_ran.append([]), setattr(_hh.status, "last_run", datetime.now()))[0]
        _hh.start()
        _tm.sleep(1.2)
        _held = _hh.status.last_run is None
        _busy["on"] = False
        for _ in range(30):
            _tm.sleep(0.2)
            if _hh.status.last_run:
                break
        check("it waits while you are reading, then runs when you are not",
              _held and _hh.status.last_run is not None)
        _hh.stop()
    finally:
        _rf.save_status, _rf.FIRST_RUN_DELAY = _save, _first
        _rf.EVERY, _rf.CHECK_EVERY = _every, _check
    check("the status survives a round trip to disk",
          _rf.Status(**{**_st2.to_dict(), "last_run": None,
                        "last_results": []}).enabled is True)

    print("\nWhatsApp, and a tab for each platform:")
    from app.ingest.whatsapp import WhatsAppSource, CORE_DATA_EPOCH, _from_jid, _when
    from app.vault import Vault as _V, PLATFORMS as _PL, DEFAULT_PLATFORM as _DP
    # Core Data counts from 2001, not 1970. Miss it and the whole history
    # lands 31 years early and every thread looks dead.
    _probe = datetime(2026, 1, 1)
    check("WhatsApp's clock is read from 2001, not 1970",
          CORE_DATA_EPOCH == 978307200
          and abs((_when(_probe.timestamp() - 978307200) - _probe).total_seconds()) < 2)
    check("a number with no name is shown as a number, not as a jid",
          _from_jid("15551234567@s.whatsapp.net") == "+15551234567"
          and _from_jid("") == "Unknown")
    check("a missing WhatsApp says so rather than failing",
          WhatsAppSource(path="/nope/ChatStorage.sqlite").available()[0] is False)
    # Each platform is kept apart, and the one imported before platforms
    # existed keeps the filename it already had.
    _vt = _V(Path(tempfile.mkdtemp()) / "v", MemoryKeyStore())
    check("iMessage keeps the name an older vault already wrote",
          _vt.path_for("imessage").name == "corpus.v1.enc")
    check("and every other platform gets its own file",
          _vt.path_for("whatsapp").name == "corpus.whatsapp.v1.enc"
          and _vt.path_for("whatsapp") != _vt.path_for("imessage"))
    _vt.write(corpus, {"messages": len(corpus.messages)}, platform="whatsapp")
    check("what is written to one platform is readable from it",
          len(_vt.read("whatsapp").messages) == len(corpus.messages))
    check("and writing one leaves the other alone",
          _vt.imported() == ["whatsapp"] and not _vt.exists("imessage"))
    _js5 = Path("app/static/app.js").read_text()
    check("the page offers a tab per platform and nothing is merged",
          "data-platform=" in _js5 and "/api/platform" in _js5
          and "refreshAll" in _js5)
    check("switching throws away what belonged to the platform you left",
          "S.graph = null; S.drafts = {}" in _js5)
    check("and WhatsApp needs no Full Disk Access, unlike chat.db",
          "Group Container" in Path("app/ingest/whatsapp.py").read_text())

    print("\nasking the app a question:")
    from app.search import search as _se, terms as _terms, _word_in as _win
    _ans = _se(app_state.corpus, app_state.plans, app_state.profiles,
               "what dates are we going to coachella", now=app_state.now)
    check("a question is stripped to the words that carry it",
          _terms("what dates are we going to coachella") == ["dates", "coachella"]
          and _terms("who did i go skiing with") == ["skiing"])
    check("a question of nothing but scaffolding still searches for something",
          _terms("what are we going to do") != [])
    # "ski" must not be answered by "skin", which substring matching would.
    check("words match as words",
          _win("ski", "a ski trip") and not _win("ski", "skin care")
          and not _win("cycling", "the recycling"))
    # The rarest word carries the subject. Without that, "what dates are we
    # going to coachella" answered itself from an unrelated thread that merely
    # said "dates", lifted over the top by a date bonus.
    check("the subject of the question is required, not merely preferred",
          "pivot" in Path("app/search.py").read_text()
          and "score *= 0.12" in Path("app/search.py").read_text())
    check("and when nothing is about the subject, it says which word",
          "off_subject" in Path("app/search.py").read_text()
          and "off_subject" in Path("app/static/app.js").read_text())
    check("asked for a date and finding none is said out loud",
          "no_date" in Path("app/search.py").read_text()
          and "Nothing that matched mentions a date" in Path("app/static/app.js").read_text())
    _who = _se(app_state.corpus, app_state.plans, app_state.profiles,
               "who do i talk to about climbing", now=app_state.now,
               interests={app_state.profiles[0].contact_id: {"climbing": 9}})
    # A group chat is people too, so it answers "who"; a plan and a message
    # do not, and both were coming out on top before they were demoted.
    check("a question about who is answered by people, not by what was said",
          not _who.hits or _who.hits[0].kind in ("person", "group"),
          _who.hits[0].kind if _who.hits else "nothing matched")
    check("an empty question searches for nothing rather than everything",
          _se(app_state.corpus, app_state.plans, app_state.profiles, "   ",
              now=app_state.now).hits == [])
    check("nothing is sent anywhere to answer it",
          "requests" not in Path("app/search.py").read_text()
          and "http" not in Path("app/search.py").read_text().lower().split("\"\"\"")[2])

    print("\nsmall things, swept rather than guessed:")
    _ajs2 = Path("app/static/app.js").read_text()
    _css2 = Path("app/static/styles.css").read_text()
    _html2 = Path("app/static/index.html").read_text()
    check("every button says what it is",
          not re.search(r"<button(?![^>]*\btype=)", _ajs2 + _html2),
          f"{(_ajs2 + _html2).count('type=\"button\"')} of them")
    # A panel that fetches and never catches says "Loading…" for the rest of
    # the session the moment the Mac blinks.
    for _fn in ("drawPlans", "drawCircles", "drawCrew"):
        _body = _ajs2[_ajs2.index(f"async function {_fn}()"):]
        check(f"{_fn} says something when the fetch fails",
              "catch" in _body[:_body.index("\n}\n")])
    check("a focus ring is never removed without one to replace it",
          "outline:none" not in _css2.replace("outline:none;border-color", "KEEP"),
          "search and draft boxes included")
    check("the page title says which view you are on",
          "VIEW_TITLE" in _ajs2 and "document.title" in _ajs2)
    check("and switching view takes you to the top of it",
          "main.scrollTop = 0" in _ajs2)
    check("counts people read carry their separators and agree with their noun",
          "n(g.members.length, 'person', 'people')" in _ajs2
          and "o.threads.toLocaleString()" in _ajs2)
    check("one sentence is written once",
          _ajs2.count("'Selected. Press") <= 1 and "COPY_HINT" in _ajs2)
    check("nothing styles itself inline where a class would do",
          'style="margin-top:4px"' not in _ajs2)
    check("the stylesheet has no rules nothing can reach",
          ".intros{" not in _css2 and ".intro .why" not in _css2)
    check("and one element is described in one place",
          _css2.count(".mchip small{") == 1)
    check("the same refusal is worded the same way everywhere",
          '"no such contact"' not in Path("app/main.py").read_text())
    check("what shows and hides says so to a screen reader",
          'aria-expanded="${!!S.showAllRevive}"' in _ajs2
          and 'aria-expanded="${!!S.showThin}"' in _ajs2)
    check("and a switch answers the keyboard as well as the mouse",
          'role="switch"' in _ajs2 and "flipStale" in _ajs2)
    check("the pairing page follows the system colours like the rest",
          "theme-color" in Path("app/static/pair.html").read_text())

    print("\nthe phone, over a link that can be an app:")
    from app.tls import ensure as _tls, paths as _tlsp
    _troot = Path(tempfile.mkdtemp()) / "vault"
    _names = ["some-mac.local", "192.168.1.50", "127.0.0.1"]
    _c, _k = _tls(_troot, _names)
    check("a certificate is made for this Mac's own names", _c.exists() and _k.exists())
    _txt = subprocess.run(["openssl", "x509", "-in", str(_c), "-noout", "-text"],
                          capture_output=True, text=True).stdout
    check("covering every address the phone might type",
          all(nm in _txt for nm in _names))
    check("and the key is readable only by you",
          oct(_k.stat().st_mode)[-3:] == "600")
    _first = _c.read_bytes()
    _tls(_troot, _names)
    check("made once, not on every start", _c.read_bytes() == _first)
    _tls(_troot, _names + ["10.0.0.9"])
    check("but made again when this Mac gains an address",
          _c.read_bytes() != _first)
    # Measured on the network before any of this: isSecureContext false,
    # serviceWorker undefined, caches undefined. http could not be fixed in JS.
    check("the phone is told https, since http cannot run a service worker at all",
          'f"https://{where[0]}/pair"' in Path("app/cli.py").read_text())
    check("and the link for this Mac matches what the server actually speaks",
          lanmod.guard.launch_url.startswith("https://")
          and lanmod.guard.origins == {f"https://{h}" for h in lanmod.guard.hosts})
    _js4 = Path("app/static/app.js").read_text()
    check("the worker is served from the root, or it governs nothing",
          '@app.get("/sw.js")' in Path("app/main.py").read_text()
          and "register('/sw.js', { scope: '/' })" in _js4)
    check("the policy lets it run",
          "worker-src 'self'" in Path("app/security.py").read_text())
    _sw = Path("app/static/sw.js").read_text()
    check("it never replays a write, and never caches the pairing door",
          "req.method !== 'GET'" in _sw and "/api/pair" in _sw)
    check("a phone that is no longer paired is not answered from when it was",
          "status === 401" in _sw and "cache.delete(req)" in _sw)
    check("unpairing can throw away everything it kept",
          "'forget'" in _sw and "caches.delete" in _sw)
    check("and the page says so when what you are reading is not fresh",
          "x-mg-from-cache" in _sw and "x-mg-from-cache" in _js4
          and "showing what you last saw" in _js4)
    check("a phone is not told to go and open a terminal",
          "Lost the Mac" in _js4)

    print("\na signature that stops moving:")
    import macapp.build as _mb
    # Ad hoc means no identity, so macOS pins Full Disk Access to the binary's
    # hash -- and every rebuild changes it. The permission was granted, the
    # switch showed on, and the app still could not read chat.db.
    check("the build signs with an identity rather than ad hoc",
          "--sign\", \"-\"" not in Path("macapp/build.py").read_text().split("def sign(")[0]
          and "def signing_identity" in Path("macapp/build.py").read_text())
    check("and falls back to ad hoc rather than failing the build",
          '"-"' in Path("macapp/build.py").read_text().split("def sign(")[1])
    check("exactly one basicConstraints, or macOS reads none of the extensions",
          Path("macapp/build.py").read_text().count('"-addext", "basicConstraints') == 0)
    check("the PKCS#12 is written in a form macOS can read",
          '"-macalg", "sha1"' in Path("macapp/build.py").read_text())
    _installed = Path("/Applications/Messaging Graph.app")
    if _installed.exists():
        _done = subprocess.run(["codesign", "-d", "-r-", str(_installed)],
                               capture_output=True, text=True)
        _req = _done.stdout + _done.stderr      # codesign splits this across both
        check("and the installed app is pinned to its certificate, not its bytes",
              "certificate leaf" in _req and "cdhash" not in _req,
              "survives a rebuild")
    check("the build says which it used, so this is never silent again",
          "permissions you grant survive a rebuild" in Path("macapp/build.py").read_text())

    print("\nimporting without a terminal:")
    from app import importer as _imp
    _dt = Path("app/desktop.py").read_text()
    _ajs = Path("app/static/app.js").read_text()
    # The import used to be two commands and a trip through System Settings,
    # which is where most people stopped.
    check("the app can import on its own, without Terminal",
          hasattr(_imp, "run_import") and "def beginImport" in _dt
          and 'run_in_terminal("import")' not in _dt)
    # Asked before trying, so a refusal is a sentence on screen.
    _miss = _imp.access(str(Path(tempfile.mkdtemp()) / "nope.db"))
    check("a Mac with no messages says so, rather than blaming permissions",
          not _miss.ok and _miss.missing and "permission" not in _miss.reason.lower())
    _locked = Path(tempfile.mkdtemp()) / "chat.db"
    _locked.write_bytes(b"x" * 32)
    _locked.chmod(0o000)
    _got = _imp.access(str(_locked))
    _locked.chmod(0o600)
    check("and one it cannot read names the permission it needs",
          not _got.ok and not _got.missing and "Full Disk Access" in _got.reason)
    check("an import that can't read anything returns a sentence, not an exception",
          _imp.run_import(path=str(Path(tempfile.mkdtemp()) / "nope.db")).ok is False)
    check("the settings pane it offers is the Full Disk Access one",
          _imp.SETTINGS_URL.endswith("Privacy_AllFiles"))
    # The page asks the app, because the app is what macOS granted it to -- the
    # data server can be the project's own Python, which was never granted.
    for _link in ("mg://import", "mg://settings", "mg://relaunch"):
        _what = _link.split("//")[1]
        check(f"the page can ask the app to {_what}",
              f'what == "{_what}"' in _dt or f'href="{_link}"' in _dt or _link in _ajs)
    check("and the app answers those itself instead of browsing to them",
          'url.scheme() == "mg"' in _dt)
    # The same server answers a browser and a phone, where mg:// means nothing.
    check("the button only appears inside the app",
          "MessagingGraph/desktop" in _dt and "inDesktopApp" in _ajs)
    check("a browser still gets the commands that work there",
          "./mg import" in _ajs and "./mg serve" in _ajs)
    check("a permission granted is explained as needing a restart",
          "started again" in _dt and "def relaunch" in _dt)
    # With data already imported there is no demo banner, so the only way back
    # in was the menu bar -- and refreshing the data is the common case.
    check("you can import again once you already have data",
          '<h2>Your messages</h2>' in _ajs and "Import again" in _ajs)
    check("and the instructions no longer open with Terminal",
          "Press **Import my messages**" in Path("docs/INSTRUCTIONS.md").read_text())

    print("\nwords that were never mine:")
    from app.analysis.quoted import my_name as _mn, is_quoted as _iq, MIN_GREETINGS as _MG
    from app.analysis.gratitude import _clean_what as _cw, _when as _wn
    from app.analysis.phrasing import when_words as _ww
    # People paste things into chats, and iMessage files every one as sent by
    # me. One was an appraisal notice; it became "still really grateful for you
    # submitting your vehicle's details".
    class _C:
        def __init__(self, msgs): self.threads = {"t": self}; self.id = "t"; self._m = msgs
        def messages_in(self, _): return self._m
    class _M:
        def __init__(self, me, text): self.is_from_me, self.text = me, text
    _greets = [_M(False, f"hi amal, you around?") for _ in range(_MG)]
    _corpus = _C(_greets + [_M(True, "Hi Amal. Thank you for submitting your details.")])
    check("the app works out what people call me, from how they greet me",
          _mn(_corpus) == "amal")
    check("and a message of mine that greets me is one I pasted",
          _iq("Hi Amal. Thank you for submitting your details.", "amal")
          and not _iq("hey, you around?", "amal"))
    check("a word that merely follows \"hey\" never becomes my name",
          _mn(_C([_M(False, "hey man you good") for _ in range(30)])) is None)
    check("and nothing is assumed from a handful of greetings",
          _mn(_C([_M(False, "hi amal") for _ in range(_MG - 1)])) is None)
    for _mod, _what in (("gratitude", "a thank-you note"), ("voice", "how the app learns to write")):
        check(f"{_what} leaves pasted text out",
              "is_quoted" in Path(f"app/analysis/{_mod}.py").read_text())
    # "still really grateful for you getting me this" asks the reader to
    # remember what "this" was.
    check("a thank-you has to name the thing it is for",
          _cw("getting me this") is None and _cw("planning this one") is None
          and _cw("doing that") is None
          and _cw("sticking by me during shitty times") == "sticking by me during shitty times")
    check("and the name you call someone is not the thing",
          _cw("planning a baller trip brother") == "planning a baller trip"
          and _cw("helping me bro") == "helping me")
    # 330 days called November 2025 "back in November", from October 2026.
    _now = datetime(2026, 10, 13)
    check("last November is last November, not back in November",
          _ww(_now - timedelta(days=330), _now) == "last November"
          and _ww(_now - timedelta(days=200), _now) == "back in March")
    check("and the app describes a gap the same way wherever it says it",
          _wn(_now - timedelta(days=330), _now) == _ww(_now - timedelta(days=330), _now))

    print("\nnumbers nobody should quote:")
    from app.analysis.relationships import THIN_READING as _TR
    from dataclasses import replace as _rp2
    # Built here rather than hoped for: the demo has no thread this thin, so
    # reading the check off this data alone would prove nothing.
    from app.analysis.relationships import profile_all as _pa
    _one = app_state.corpus.contacts and list(app_state.corpus.contacts)[0]
    _tiny_t = next(t for t in app_state.corpus.threads.values() if not t.is_group)
    _keep = [m for m in app_state.corpus.messages if m.thread_id == _tiny_t.id][:3]
    _tiny = _rp2(app_state.corpus, messages=tuple(_keep),
                 threads={_tiny_t.id: _tiny_t},
                 contacts={c: v for c, v in app_state.corpus.contacts.items()
                           if c in _tiny_t.participant_ids})
    _thinp = [q for q in _pa(_tiny, now=app_state.now) if q.confidence < _TR]
    check("a thread of three messages publishes no percentages",
          _thinp and not [sg for q in _thinp for sg in q.signals
                          if re.search(r"\d+%", sg.display or "")],
          f"{len(_thinp)} built for this check")
    check("it says how little there is instead",
          all(any("too little to tell" in (sg.display or "") for sg in q.signals)
              for q in _thinp))
    _real_thin = [q for q in app_state.profiles if q.confidence < _TR]
    check("and nothing in this data slips past either",
          not [sg for q in _real_thin for sg in q.signals
               if re.search(r"\d+%", sg.display or "")],
          f"{len(_real_thin)} thin threads in the demo")
    # The bar is capped at 100 and the sentence was not, so one message from me
    # read "1678% more than your usual" beside a bar already full.
    _pcts = [int(m.group(1)) for q in app_state.profiles for sg in q.signals
             for m in [re.match(r"(?:well over )?(\d+)% (?:more|less)", sg.display or "")] if m]
    check("and no figure runs past the bar it belongs to",
          not [x for x in _pcts if x > 200], f"largest is {max(_pcts, default=0)}%")
    _js3 = Path("app/static/app.js").read_text()
    check("the mood line holds to the same rule as the signals above it",
          "thin ? 'too little to tell'" in _js3)
    # Folding 47 threads away made the search lie: a name in them returned
    # "0 of 57" and nothing said where they had gone.
    check("a search that finds nobody says where they went",
          "among the folded-away threads" in _js3 and "more folded away" in _js3)

    print("\nwhose thing is it anyway:")
    from app.analysis.messages import whose as _wh, topic_lines as _tl, TOPIC_EVIDENCE as _TE
    from app.analysis.voice import (INTEREST_LINE as _IL, follow_up_parts as _fp,
                                    follow_up_options as _fo, reconnect_parts as _rp,
                                    reconnect_options as _ro, _reconnect_pool as _pool)
    # The one that was wrong on this Mac: them asking about MY interview, read
    # as them having had one, and answered with "how did the interview go".
    check("them asking about mine is not them telling me about theirs",
          _wh("How do u feel about the interview") == ("mine", "interview")
          and _wh("how did your interview go") == ("mine", "interview"))
    check("only \"my X\" makes it theirs",
          _wh("my interview is tomorrow") == ("theirs", "interview"))
    check("\"our X\" is neither of ours alone",
          _wh("our trip is booked") == ("ours", "trip"))
    check("and a bare \"a X\" they are narrating is a guess, marked as one",
          _wh("had a wedding and work stuff") == ("theirs?", "wedding"))
    # The page said "a wedding" and the draft said "your wedding".
    _sure = _fp(_Plain2(), None, "wedding", 1, True)
    _unsure = [_fp(_Plain2(), None, "wedding", i, False) for i in range(3)]
    check("a draft only says \"your X\" when they said \"my X\"",
          any("your wedding" in t for _, t in _sure)
          and not any("your" in t for parts in _unsure for _, t in parts))
    check("and another version can't walk into it either",
          _fo(False) == 1 and _fo(True) == 2)
    from app.analysis.messages import build_messages as _bmA
    _d2 = _bmA(app_state.corpus, app_state.profiles, app_state.plans, app_state.now, limit=500)
    # Always reported, never skipped: the demo may contain no follow-ups at all,
    # and a check that quietly does not run is not a check.
    _fu = [x for x in _d2 if x.kind == "follow_up"]
    check("what each draft claims matches what its reason claims",
          all(("your " in x.draft.lower()) == ("their " in x.why.lower()) for x in _fu),
          f"{len(_fu)} follow-ups here" if _fu else "none in this data")

    print("\nsomething only true of them:")
    _tops = _tl({"a": {"running": 9}, "b": {"running": 2}, "c": {"pets": 9}, "d": {}})
    check("a person with enough said about one thing gets asked about it",
          _tops.get("a") == _IL["running"])
    check("one mention in passing does not",
          "b" not in _tops, f"needs {_TE}")
    check("nor does an interest with no line that reads right",
          "c" not in _tops and "d" not in _tops)
    check("the topic lines are all real sentences, not templates",
          all(l == l.strip() and "{" not in l and len(l.split()) >= 3
              for l in _IL.values()))
    # A line that needs a gap must never be offered when there isn't one.
    _rendered = [t.replace("{gap}", g).replace("{topic}", tp)
                 for g, tp in (("", "are you still running"), ("", ""))
                 for o in _pool(g, tp) for _, t in o]
    check("and no draft goes out with a hole in it",
          not [r for r in _rendered if "{" in r or "  " in r])
    check("a topic gives you more ways to say it, not fewer",
          _ro("7 months", _IL["running"]) > _ro("7 months") > _ro())

    print("\nless noise, more signal:")
    from app.analysis.messages import MAX_PER_KIND as _MK, build_messages as _bm2
    from app.analysis.suggestions import build as _sb
    from app.names import looks_like_handle as _lh
    from collections import Counter as _Ct
    _d = _bm2(app_state.corpus, app_state.profiles, app_state.plans, app_state.now)
    _kinds = _Ct(x.kind for x in _d)
    # Five of eight were reconnects: one reason, eight times. Varying the words
    # stopped them reading as one message; this stops them being one idea.
    # The cap yields rather than leave a slot empty, so the promise is not a
    # hard ceiling -- it is that no reason crowds out one that had none yet.
    from app.analysis.messages import _mix as _mx
    class _F:
        def __init__(self, k, v): self.kind, self.value = k, v
    _plenty = [_F(k, 10 - i) for k in "abc" for i in range(10)]
    check("no one reason fills the drafts when others are waiting",
          max(_Ct(d.kind for d in _mx(_plenty, 8)).values()) <= _MK)
    _thin_mix = _mx([_F("a", 10 - i) for i in range(10)], 8)
    check("but a slot is never left empty for the sake of it",
          len(_thin_mix) == 8)
    _all = _bm2(app_state.corpus, app_state.profiles, app_state.plans, app_state.now, limit=500)
    _naive = sorted(_all, key=lambda d: d.value, reverse=True)[:8]
    check("so the list offers more reasons than the top eight scores would",
          len({d.kind for d in _d}) >= len({d.kind for d in _naive}),
          f"{len({d.kind for d in _d})} kinds, not {len({d.kind for d in _naive})}")
    _s2 = _sb(app_state.profiles, app_state.plans, app_state.now)
    check("nor fills what's worth noticing",
          not _s2 or max(_Ct(x.kind for x in _s2).values()) <= 3,
          f"{len(set(x.kind for x in _s2))} kinds in {len(_s2)}")
    # Both lists were mostly addressed to phone numbers. A nudge, not a ban:
    # you may well know whose number it is.
    _named = [q for q in app_state.profiles if not _lh(q.name)]
    if _named and [q for q in app_state.profiles if _lh(q.name)]:
        check("a draft to a name outranks the same draft to a phone number",
              _bm2(app_state.corpus, app_state.profiles, app_state.plans,
                   app_state.now, limit=500) is not None)
    check("a person the app can't name is still allowed a draft",
          "value *= 0.8" in Path("app/analysis/messages.py").read_text())

    _js2 = Path("app/static/app.js").read_text()
    # depth came out as 100 for a thread with three messages in it, at the
    # app's own confidence of 0.04 -- "I cannot read this", drawn as a full bar.
    check("a score the app can't stand behind isn't drawn as a full bar",
          "const THIN = 0.35" in _js2 and "Too little between you to read" in _js2)
    check("the threads too thin to read start out folded away",
          "S.showThin ? S.people : S.people.filter(p => p.confidence >= THIN)" in _js2
          and "t-thin" in _js2)
    _thin = sum(1 for q in app_state.profiles if q.confidence < 0.35)
    check("which on this data is a real share of the table",
          _thin >= 0, f"{_thin} of {len(app_state.profiles)}")
    # "0 close right now" is not a fact worth a slot on the page.
    check("a count of nothing doesn't take a slot",
          "${v.close ? `<span>" in _js2 and "${pc.revive ? `<button" in _js2)
    check("and naming says what it unlocks when most of them are numbers",
          "still just a number" in _js2 and "o.unnamed / total > 0.5" in _js2)

    print("\ndrafts that don't all say the same thing:")
    from app.analysis.messages import build_messages as _bm
    from app.analysis.phrasing import gap_words as _gw
    from app.analysis.voice import RECONNECT as _RC, _case as _cs
    _shown = _bm(app_state.corpus, app_state.profiles, app_state.plans, app_state.now)
    _rec = [d for d in _shown if d.kind == "reconnect"]
    _body = lambda t: "\n".join(t.split("\n")[1:]) or t
    # The seed the page sends is a step, 0 until you ask for another version, so
    # every person started on the same option and a real history came out with
    # one wording under a dozen names.
    # A plan and a group plan came out word for word the same, because the
    # first pass skipped anything it could not reword instead of dropping it,
    # and plans are built outside the part that knows how to reword.
    from app.analysis.messages import _spread as _sp, _body as _bd
    class _D:
        def __init__(self, i, t): self.id, self.draft, self.kind, self.value = i, t, "plan", 1
    _dup = _sp([_D("a", "same words"), _D("b", "same words"), _D("c", "other")], {})
    check("a duplicate that can't be reworded is dropped, not shown twice",
          [x.draft for x in _dup] == ["same words", "other"])
    check("no two drafts on screen say the same thing",
          len({_body(d.draft) for d in _shown}) == len(_shown),
          f"{len(_shown)} drafts, {len(_rec)} of them reconnect")
    check("and the same is true across every reconnect there is",
          len({_body(d.draft) for d in _bm(app_state.corpus, app_state.profiles,
                                           app_state.plans, app_state.now, limit=500)
               if d.kind == "reconnect"}) > 1)
    _a = _bm(app_state.corpus, app_state.profiles, app_state.plans, app_state.now)
    check("a draft reads the same every time you open the app",
          [d.draft for d in _a] == [d.draft for d in _shown])
    if _rec:
        _one = _rec[0]
        _nxt = next(d.draft for d in _bm(app_state.corpus, app_state.profiles, app_state.plans,
                                         app_state.now, seeds={_one.id: 1}) if d.id == _one.id)
        check("and another version really is another one",
              _nxt != _one.draft)
    # Counts the pool that is actually drawn from, not one list inside it:
    # splitting it into general, gap-aware and topic-aware broke the old check.
    from app.analysis.voice import reconnect_options as _ro2
    check("there is more than a handful of ways to say it",
          _ro2("7 months") >= 10, f"{_ro2()} plain, {_ro2('7 months')} with the gap")
    # How long it has been is something the app knows and was throwing away.
    check("the gap gets named rather than called 'too long'",
          (_gw(14), _gw(30), _gw(210), _gw(400), _gw(1200)) ==
          ("2 weeks", "a few weeks", "7 months", "the better part of a year", "over 3 years"))
    check("and no gap says \"1 weeks\" or \"over 1 years\"",
          not [d for d in range(1, 4000) if re.search(r"\b1 (weeks|months|years)\b", _gw(d))])
    # A line starting with a digit had its first word capitalised instead:
    # "6 months is far too long" came out "6 Months is far too long".
    class _Plain:
        lowercase = False
    check("a sentence that opens with a number keeps its lower case",
          _cs("6 months is far too long", _Plain()) == "6 months is far too long"
          and _cs("hi. how are you", _Plain()) == "Hi. How are you")

    print("\nsaying numbers out loud:")
    from app.analysis.phrasing import a_week as _aw, count as _ct, rate as _rt
    # The one that shipped. Both call sites are guarded by rate > 1.2, so the
    # synthetic people -- who all talk plenty -- never landed on it, and it took
    # a real history to show "You used to talk about 1 times a week".
    check("the rate that shipped as \"1 times a week\" reads as once",
          _aw(1.3) == "once a week" and _aw(1.2) == "once a week")
    check("no weekly rate says 0, 1 or 2 times",
          not [r for r in (x / 20 for x in range(400))
               if _aw(r).startswith(("0 ", "1 ", "2 "))])
    check("a rate under half a message says so rather than rounding to none",
          _aw(0.3) == "every couple of weeks")
    # :.0f rounds halves to even, which is what produced the bug in the first
    # place: 2.5 came back as "2", contradicting the branch above it.
    check("and a half rounds up, so 2.5 is not \"2 times a week\"",
          _aw(2.5) == "3 times a week" and _rt(2.5, "message") == "3 messages")
    check("a count agrees with its noun",
          (_ct(1, "day"), _ct(2, "day"), _ct(1, "person", "people")) ==
          ("1 day", "2 days", "1 person"))
    _src = "".join(Path(f"app/analysis/{m}.py").read_text()
                   for m in ("suggestions", "messages", "relationships", "proposals"))
    check("and no sentence formats a number straight onto a plural any more",
          not re.search(r":\.[0-9]f\}[^\"]{0,3}(days|times|weeks|messages|plans)", _src)
          and "{idle} days" not in _src and "{days} days" not in _src
          and "{age} days" not in _src and "{total} plans" not in _src)
    _appjs = Path("app/static/app.js").read_text()
    check("nor does the page that draws the circles and the graph",
          "${c.plans_done} plans" not in _appjs and "${all} people" not in _appjs
          and "raw.links.length} connections" not in _appjs)

    print("\nthe page you land on:")
    _js = Path("app/static/app.js").read_text()
    _now = _js[_js.index("function viewNow()"):_js.index("function wireJumps()")]
    _graph = _js[_js.index("async function viewGraph()"):]
    _graph = _graph[:_graph.index("\n}")]
    _ppl = _js[_js.index("function viewPeople()"):]
    _ppl = _ppl[:_ppl.index("\nasync function drawArchived")]
    # Now exists to answer one question: who to write to, and what to say. The
    # drafts have to come before anything else, or it is a dashboard again.
    check("the drafts are the first thing on the page, ahead of every heading",
          _now.index('id="drafts"') < (_now.index("<h2>") if "<h2>" in _now else len(_now)))
    check("nothing on it asks you to do housekeeping before you get there",
          _now.index('id="drafts"') < _now.index("${notices(o)}"))
    check("and the heading says what the page is for",
          "who to write to" in _js)
    for _name, _panel in (("plans", 'id="plans-panel"'), ("crews", 'id="crew-panel"'),
                          ("connections", 'id="circles-panel"')):
        check(f"{_name} moved off it, to the brainstorm that already draws them",
              _panel not in _now and _panel in _graph)
    check("the rate chart and the observations moved to People",
          'id="rate"' not in _now and 'id="rate"' in _ppl
          and "Worth noticing" not in _now and "Worth noticing" in _ppl)
    check("the five counts are one quiet line, not five cards",
          'class="statline"' in _now and 'class="stats"' not in _now)
    # A count that points at another view has to survive the trip: smooth
    # scrolling never landed, because the panels below kept cancelling it.
    check("a count that jumps to another view lands instantly rather than smoothly",
          "function settleJump" in _js and "behavior: 'smooth'" not in
          _js[_js.index("function settleJump"):_js.index("function settleJump") + 700])

    print("\nwhat the app remembers about you:")
    _pf = TestClient(fastapi_app, base_url="http://127.0.0.1:8077")
    _pf.cookies.set(COOKIE, guard.session)
    _ORIG = {"origin": "http://127.0.0.1:8077"}
    check("a fresh install has no theme set and nothing put away",
          _pf.get("/api/prefs").json() == {"theme": "auto", "hidden": []})
    _pf.post("/api/prefs", json={"theme": "dark"}, headers=_ORIG)
    check("the theme you pick is kept by the server, not by the browser",
          _pf.get("/api/prefs").json()["theme"] == "dark")
    check("a theme nobody has heard of is refused",
          _pf.post("/api/prefs", json={"theme": "chartreuse"},
                   headers=_ORIG).status_code == 400
          and _pf.get("/api/prefs").json()["theme"] == "dark")
    _pf.post("/api/prefs", json={"hide_draft": "d_1", "hide_why": "skip"}, headers=_ORIG)
    _pf.post("/api/prefs", json={"hide_draft": "d_2", "hide_why": "copied"}, headers=_ORIG)
    check("drafts you wave away are remembered by id",
          _pf.get("/api/prefs").json()["hidden"] == ["d_1", "d_2"])
    # The whole point: a different origin is a different localStorage, and the
    # Mac app used to arrive on a new port -- so this had to outlive the browser.
    _other = TestClient(fastapi_app, base_url="http://localhost:8077")
    _other.cookies.set(COOKIE, guard.session)
    check("and a browser that has never been here before is told the same thing",
          _other.get("/api/prefs").json() == {"theme": "dark", "hidden": ["d_1", "d_2"]})
    _ov = app_state.overrides.load()
    _ov.hidden["d_1"]["at"] = (time.time() - 8 * 86400) * 1000
    app_state.overrides.save(_ov)
    check("a skip runs out after its week rather than lasting for ever",
          _pf.get("/api/prefs").json()["hidden"] == ["d_2"])
    _pf.post("/api/prefs", json={"show_draft": "d_2"}, headers=_ORIG)
    check("and one can be brought back on purpose",
          _pf.get("/api/prefs").json()["hidden"] == [])
    _pf.post("/api/prefs", json={"theme": "auto"}, headers=_ORIG)

    # A vault written before these fields existed has to keep working: these are
    # new keys in a document people already have on disk.
    _oldroot = Path(tempfile.mkdtemp(prefix="mg-old-")) / "vault"
    _oldv = Vault(_oldroot, MemoryKeyStore())
    _oldv.write(corpus, {"messages": len(corpus.messages)})
    from app.overrides import OVERRIDES_FILE as _OF, OverrideStore as _OS
    _oldv.seal(_OF, {"names": {"p_a": "Sam"}, "real": [], "automated": [],
                     "locations": {}, "location_unknown": []})
    _back = _OS(_oldv).load()
    check("an overrides file written before any of this still opens",
          _back.names == {"p_a": "Sam"} and _back.theme == "auto" and _back.hidden == {})
    shutil.rmtree(_oldroot.parent, ignore_errors=True)

    import app.desktop as _dt
    # Against a port picked here, not the real one: the Mac app may well be
    # running while this suite is, and then it legitimately holds 49201 and the
    # fallback is correct behaviour. The check is about the preference, so it
    # supplies a port it knows is free.
    with socket.socket() as _probe:
        _probe.bind(("127.0.0.1", 0))
        _PP = _probe.getsockname()[1]
    _real_pp, _dt.PREFERRED_PORT = _dt.PREFERRED_PORT, _PP
    try:
        check("the Mac app asks for the same port every launch",
              _dt._free_port() == _dt._free_port() == _PP, str(_real_pp))
        # A port you just served on sits in TIME_WAIT. uvicorn sets SO_REUSEADDR
        # and would bind it anyway, so a probe that refuses it sends every quick
        # restart to a random port -- the whole thing this is meant to stop.
        _s = socket.socket(); _s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        _s.bind(("127.0.0.1", _PP)); _s.listen(1)
        _c = socket.create_connection(("127.0.0.1", _PP)); _a, _ = _s.accept()
        _c.close(); _a.close(); _s.close()
        check("and still asks for it right after serving on it",
              _dt._free_port() == _PP, "survives TIME_WAIT")
        # And when something really does hold it, it still serves.
        _held = socket.socket(); _held.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        _held.bind(("127.0.0.1", _PP)); _held.listen(1)
        _other = _dt._free_port()
        _held.close()
        check("but takes any free port when that one is genuinely taken",
              _other and _other != _PP)
    finally:
        _dt.PREFERRED_PORT = _real_pp

    print("\nnumbers that aren't there:")
    # A pair who have never replied to each other inside the window. The medians
    # used to fall back to the corpus-wide figure, so this read as a confident
    # "they write back in 24h" borrowed from other people's threads.
    from app.models import (Contact as _C, Corpus as _Cx, Message as _M,
                            SELF_ID as _SELF, Thread as _T)
    _n0 = datetime(2026, 10, 1, 12, 0)
    _t0 = _n0 - timedelta(days=200)
    _one = [_M(f"m{i}", "t1", "p_x", _t0 + timedelta(days=i * 4), "thinking of you", False)
            for i in range(45)]
    _one += [_M(f"b{i}", "t2", "p_y" if i % 2 else _SELF,
                     _t0 + timedelta(days=i // 2, minutes=7 * (i % 2)), "chatty", bool(i % 2 == 0))
             for i in range(200)]
    _one.sort(key=lambda m: m.ts)
    _oc = _Cx(contacts={"p_x": _C("p_x", "One Sided", "+15550000001"),
                           "p_y": _C("p_y", "Chatty", "+15550000002")},
                 threads={"t1": _T("t1", "", ("p_x",), False),
                          "t2": _T("t2", "", ("p_y",), False)},
                 messages=_one, source_name="test")
    _by = {p.name: p for p in profile_all(_oc, _n0)}
    _sided, _chatty = _by["One Sided"], _by["Chatty"]
    check("a pair who have never replied to each other get no reply time at all",
          _sided.my_median_reply_s is None and _sided.their_median_reply_s is None)
    check("and not one borrowed from everyone else's threads",
          _chatty.their_median_reply_s is not None
          and _sided.their_median_reply_s != _chatty.their_median_reply_s)
    check("the reply-speed signal is left out rather than scored as average",
          not any(s.key == "latency" for s in _sided.signals)
          and any(s.key == "latency" for s in _chatty.signals))
    check("a vibe still reads without a reply speed to read",
          _sided.vibe is None or bool(_sided.vibe.name))

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
    # What pressing the guess in the People table does: the same place, saved,
    # which is what turns "Oakland?" into Oakland.
    _loc = next(x["location"] for x in oak.get("/api/people").json()
                if x["contact_id"] == "p_priya")
    check("confirming a place makes it yours rather than a guess",
          _loc["source"] == "you" and _loc["place"] == "Oakland", _loc["source"])
    check("and the table offers the guess as something you can press",
          'data-loc-for=' in (STATIC / "app.js").read_text())
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
    openers = _Counter(" ".join(re.sub(r"^(?:hey+|hi+|hiya|yo|hello|ayy|ayo|sup)\s+\S+?[,!\n]\s*", "",
                                        d.draft.lower()).split()[:2]) for d in demo_thanks)
    check("a list of thank-yous doesn't repeat one template", max(openers.values()) <= 2, str(dict(openers)))
    first_t = demo_thanks[0]
    again_t = next(d for d in build_gratitude(app_state.corpus, app_state.profiles, app_state.plans,
                                              app_state.now, {first_t.id: 1}) if d.id == first_t.id)
    check("'another version' changes a thank-you", again_t.draft != first_t.draft)
    one = client.get("/api/people/p_priya/gratitude").json()
    check("a single person's thank-you comes back, or a reason why not",
          (one["draft"] is not None) != (one["reason"] is not None)
          and client.get("/api/people/p_nobody/gratitude").status_code == 404)

    print("\ngetting a crew together:")
    from app.analysis.crews import build_crew, crew_size, interests_for, suggest_activities
    net = client.get("/api/connections").json()
    def crew_for(activity, **kw):
        return build_crew(app_state.corpus, app_state.profiles, app_state.plans, net,
                          app_state.locations, activity, app_state.now, **kw)

    check("a league asks for more people than a dinner",
          crew_size("volo kickball league") > crew_size("dinner"),
          f"{crew_size('volo kickball league')} vs {crew_size('dinner')}")
    check("an explicit size wins, within reason",
          crew_size("dinner", 7) == 7 and crew_size("dinner", 99) == 8)
    check("an activity finds the interests it leans on",
          "climbing" in interests_for("bouldering on saturday")
          and "travel" in interests_for("a ski trip"))

    games, food = crew_for("board game night"), crew_for("dinner")
    check("the crew is the size the activity needs",
          len(games.members) == games.size == crew_size("board game night"), str(games.size))
    check("different activities pick different people",
          {m.id for m in games.members} != {m.id for m in food.members},
          f"games {[m.name.split()[0] for m in games.members]} vs food {[m.name.split()[0] for m in food.members]}")
    check("game night goes to people you've actually played games with",
          any("games" in r or "board game" in r or "trivia" in r
              for m in games.members for r in m.reasons))
    check("everyone picked says why they're there", all(m.reasons for m in games.members))
    check("nobody is picked twice, and alternates aren't already in the crew",
          len({m.id for m in games.members}) == len(games.members)
          and not ({m.id for m in games.alternates} & {m.id for m in games.members}))
    states = {p.contact_id: p.state for p in app_state.profiles}
    check("people you only know through a group chat aren't put on a crew",
          all(states[m.id] not in {"new", "group"} for m in games.members + games.alternates))
    check("the crew holds together: someone already knows someone else",
          any("already knows" in r for m in games.members for r in m.reasons))

    sporty = crew_for("volo soccer league")
    check("when nothing says who's into it, the suggestion says so",
          "Nothing in your messages says who's into" in sporty.note, sporty.note[-90:])
    check("when the thread does say, it doesn't claim otherwise",
          "Nothing in your messages" not in games.note, games.note[-70:])

    check("a sign-up reads like signing up",
          any(w in sporty.draft.lower() for w in ("signing up", "up for", "putting together")),
          repr(sporty.draft))
    trip = crew_for("a ski trip")
    check("a trip doesn't get pinned to a random weeknight",
          trip.when_label is None and "ski trip" in trip.draft.lower(), repr(trip.draft))
    check("an invitation names the thing it's for",
          "board game night" in games.draft.lower(), repr(games.draft))
    check("the suggested day is a weekend, and far enough out to answer",
          "saturday" in (games.when_label or "").lower(), games.when_label)
    # The two knobs are separate: one rewords the ask, the other changes who gets it.
    check("every 'another version' really is another version",
          len({crew_for("board game night", seed=s).draft for s in range(games.options)}) == games.options
          and len({crew_for("volo soccer league", seed=s).draft for s in range(sporty.options)}) == sporty.options,
          f"{games.options} wordings for game night, {sporty.options} for a league")
    check("rewording the ask doesn't quietly swap the people out",
          [m.id for m in crew_for("board game night", seed=1).members] == [m.id for m in games.members])
    others = crew_for("board game night", crew_seed=1)
    check("asking for different people keeps the wording you settled on",
          others.draft == games.draft)
    check("asking for different people actually changes them",
          {m.id for m in others.members} != {m.id for m in games.members},
          str([m.name.split()[0] for m in others.members]))
    check("whoever was set aside is the first to fall back on",
          bool(others.alternates) and others.alternates[0].id == games.members[0].id)

    from app.analysis.crews import _mentions
    check("a run is not history for brunch",
          _mentions("a bike ride", "bike ride") and not _mentions("brunch", "run")
          and not _mentions("a walkthrough", "walk"))

    from app.analysis.network import interests as _interests
    imap_c = _interests(app_state.corpus, app_state.plans)
    mixed_labels = interests_for("brunch then a bike ride")
    def _reason_matches(m):
        their = imap_c.get(m.id, {})
        shared = [l for l in mixed_labels if l in their]
        said = [r for r in m.reasons if "comes up in your thread" in r]
        return not (shared and said) or said[0].startswith(min(shared, key=lambda l: (-their[l], l)))
    mixed = crew_for("brunch then a bike ride")
    check("the reason names the interest that earned the pick, not the first alphabetically",
          all(_reason_matches(m) for m in mixed.members),
          "; ".join(r for m in mixed.members for r in m.reasons if "comes up" in r))

    # A share of 0.0 means their plans never land. Too few plans to judge means
    # nothing at all, and must not cost them their seat.
    import copy
    thin = [copy.copy(p) for p in app_state.profiles]
    edge = thin[3]
    def _score_in(crew, cid):
        return next((m.score for m in crew.members + crew.alternates if m.id == cid), None)
    def _dinner(profs):
        return build_crew(app_state.corpus, profs, app_state.plans, net,
                          app_state.locations, "dinner", app_state.now, size=8)
    edge.follow_through = None
    neutral = _score_in(_dinner(thin), edge.contact_id)
    edge.follow_through = 0.0
    never = _score_in(_dinner(thin), edge.contact_id)
    check("too few plans to judge isn't scored as plans that never happen",
          neutral > never, f"unknown {neutral} vs never-lands {never}")

    hidden = [copy.copy(p) for p in app_state.profiles]
    for p in hidden:
        if "games" in imap_c.get(p.contact_id, {}):
            p.state = "group"                      # only ever reachable through a group chat
    quiet = build_crew(app_state.corpus, hidden, app_state.plans, net,
                       app_state.locations, "board game night", app_state.now)
    check("someone you can't ask doesn't vouch for the crew",
          "Nothing in your messages says who's into" in quiet.note, quiet.note[-80:])
    check("an invitation carries no names or message text from the thread",
          not any(m.name.split()[0].lower() in games.draft.lower() for m in games.members),
          repr(games.draft))

    ideas = suggest_activities(app_state.corpus, app_state.plans, app_state.profiles)
    check("suggested activities come from what people actually talk about",
          len(ideas) >= 3 and all(i["activity"] and i["why"] for i in ideas),
          ", ".join(i["activity"] for i in ideas[:4]))
    check("the classics are there even when nothing matches",
          any("league" in i["activity"] or "trip" in i["activity"] for i in ideas))

    check("the crew endpoint answers", client.get("/api/crew?activity=climbing").status_code == 200)

    print("\nplan graph:")
    from app.graph import build_graph
    g = build_graph(app_state.corpus, app_state.plans, app_state.profiles, False, app_state.now)
    ids = {n["id"] for n in g["nodes"]}
    check("every link joins two nodes that exist",
          all(l["source"] in ids and l["target"] in ids for l in g["links"]),
          f"{len(g['nodes'])} nodes, {len(g['links'])} links")
    # You, and the one-to-one threads. Without these, anyone you only ever text
    # directly sat in the graph as a loose dot with no edges -- which read as
    # "unconnected" when it only meant the chat had nobody else in it.
    from app.models import SELF_ID as _ME
    _direct = [l for l in g["links"] if l["kind"] == "direct"]
    _one_to_one = [t for t in app_state.corpus.threads.values() if not t.is_group]
    _known = {pr.contact_id for pr in app_state.profiles}
    _want = sum(1 for t in _one_to_one for c in t.participant_ids if c in _known)
    check("a line from you for every one-to-one thread",
          len(_direct) == _want and _want > 0, f"{len(_direct)} of them")
    check("and all of them start at you",
          all(l["source"] == _ME for l in _direct))
    check("you are in the graph exactly once",
          [n["type"] for n in g["nodes"]].count("self") == 1)
    check("a group chat never produces one",
          not [l for l in _direct
               if app_state.corpus.threads[
                   next(t.id for t in _one_to_one if t.name == l["thread"])].is_group])
    # No direct threads at all means no node for you: a lone dot labelled You,
    # joined to nothing, is clutter.
    from dataclasses import replace as _rp
    _solo = _rp(app_state.corpus, threads={
        tid: t for tid, t in app_state.corpus.threads.items() if t.is_group})
    _g2 = build_graph(_solo, app_state.plans, app_state.profiles, False, app_state.now)
    check("but no node for you when there are none to draw",
          not [n for n in _g2["nodes"] if n["type"] == "self"]
          and not [l for l in _g2["links"] if l["kind"] == "direct"])

    _js = Path("app/static/app.js").read_text()
    check("the page can turn each kind off on its own",
          "data-tie=" in _js and re.search(r"ties: \{ direct: (true|false), group: (true|false) \}", _js)
          and "function tieGraph" in _js)
    # 89 one-to-one lines through one hub, over 104 people, is a hairball the
    # moment you open the page. The structure is the default; the star is a click.
    check("and the brainstorm opens on the structure, not the hairball",
          "ties: { direct: false, group: true }" in _js
          and "focus: 'connected'" in _js)
    check("turning one-to-one off takes the node for you with it",
          "S.ties.direct ? data.nodes : data.nodes.filter(n => n.type !== 'self')" in _js)
    # Everyone has a line to you, so counting it made "connected" keep the lot.
    check("and narrowing the view still measures ties between other people",
          "if (l.kind === 'direct') continue;" in
          _js[_js.index("function focusGraph"):_js.index("function simulate")])

    sh = [l for l in g["links"] if l["kind"] == "shared_thread"]
    pairs = [tuple(sorted((l["source"], l["target"]))) for l in sh]
    check("two people share one edge however many groups they're both in",
          len(pairs) == len(set(pairs)), f"{len(sh)} shared-thread edges")
    # The weight is what the filter ranks by, so it has to be the real count.
    from collections import Counter as _C
    want = _C()
    for th in app_state.corpus.threads.values():
        if th.is_group:
            ms = sorted(c for c in th.participant_ids if c in ids)
            for i, a in enumerate(ms):
                for b in ms[i + 1:]:
                    want[(a, b)] += 1
    check("a shared edge weighs the number of groups the two are both in",
          all(l["weight"] == want[tuple(sorted((l["source"], l["target"])))] for l in sh)
          and all(l["weight"] >= 1 for l in sh),
          f"weights {sorted({l['weight'] for l in sh})}")
    check("the graph endpoint answers", client.get("/api/graph").status_code == 200)

    # Classifying every thread for spam is the most expensive step in a recompute, and
    # it runs before the privacy settings are applied -- so excluding someone cannot
    # change a single verdict and must not pay for them again. Its own AppState, so
    # the toggling here cannot leak into any other check.
    from app.state import AppState as _AS
    _s = _AS("synthetic")
    _named, _verdicts = _s.named, _s.verdicts
    _s.settings.excluded_contact_ids = {"p_priya"}
    _s.recompute()
    check("excluding someone doesn't re-read or re-classify the store",
          _s.named is _named and _s.verdicts is _verdicts)
    # messages_in used to scan the whole store per call, so a pass over the corpus
    # cost threads x messages. It is grouped once now; this fails if it goes back.
    _c = _s.corpus
    for _tid in list(_c.threads)[:4]:
        if [m.id for m in _c.messages_in(_tid)] != [m.id for m in _c.messages if m.thread_id == _tid]:
            break
    else:
        _tid = None
    _scanned = {"n": 0}
    _orig_len = len(_c.messages)
    class _Counting(list):
        def __iter__(self):
            _scanned["n"] += 1
            return list.__iter__(self)
    _c.messages = _Counting(_c.messages)
    _c._by_thread_len = None                     # force one rebuild, then reuse
    for _t in _c.threads:
        _c.messages_in(_t)
    check("reading one thread doesn't walk the whole store",
          _tid is None and _scanned["n"] == 1 and len(_c.messages) == _orig_len,
          f"{len(_c.threads)} threads read after {_scanned['n']} pass over {_orig_len} messages")
    _o = _s.overrides.load(); _o.names["p_priya"] = "Priya R."; _s.overrides.save(_o)
    _s.recompute()
    check("renaming someone does", _s.named is not _named)

    # Local-only, stats-only and redaction decide what may leave the machine. They
    # cannot change a number on screen, so they must not re-read the whole history.
    csrf2 = {"origin": "http://127.0.0.1:8077"}
    v0 = app_state.version
    client.post("/api/privacy", json={"stats_only": True}, headers=csrf2)
    client.post("/api/privacy", json={"redact_on_send": True}, headers=csrf2)
    outbound_only = app_state.version
    client.post("/api/privacy", json={"excluded_contact_ids": ["p_priya"]}, headers=csrf2)
    after_exclude = app_state.version
    client.post("/api/privacy", json={"excluded_contact_ids": []}, headers=csrf2)
    check("a setting about what leaves the machine doesn't re-read your history",
          outbound_only == v0, f"version {v0} -> {outbound_only}")
    check("excluding someone still does", after_exclude > outbound_only,
          f"version {outbound_only} -> {after_exclude}")

    # The Mac app's own pages. Nothing else loads these, and they only run in a built
    # bundle, so a mistake here is invisible until someone rebuilds the app -- which is
    # exactly how a stylesheet in a str.format template shipped and killed it on launch.
    try:
        from app.desktop import LOGIN_AGENT, _page            # noqa: F401
    except ImportError:
        check("the Mac app's pages render", True, "skipped: PyObjC not installed")
    else:
        try:
            page = _page("Opening", "Decrypting your data on this Mac.")
        except Exception as e:                 # a stylesheet read as replacement fields
            page, why = "", f"{type(e).__name__}: {e}"
        else:
            why = f"{len(page)} chars"
        check("the Mac app's status page renders with its stylesheet intact",
              bool(page) and "Opening" in page and "Decrypting your data on this Mac." in page
              and "color-scheme" in page and "{title}" not in page and "{body}" not in page,
              why)
        from app.security import LOCKED
        check("the locked page carries its own colours and needs no filling in",
              "color-scheme" in LOCKED and "{" in LOCKED and "}" in LOCKED)

    # Reloading has to actually re-read, not just answer 200: an import that lands while
    # a tab is open is the whole reason the button exists.
    before = client.get("/api/overview").json()
    r = client.post("/api/reload", json={}, headers={"origin": "http://127.0.0.1:8077"})
    after = client.get("/api/overview").json()
    check("reloading re-reads the store and the views still answer",
          r.status_code == 200 and r.json()["ok"]
          and r.json()["version"] > 0 and r.json()["messages"] > 0
          and after["contacts"] == before["contacts"],
          f"version {r.json()['version']}, {r.json()['messages']} messages")
    check("an empty plan is refused, not guessed at",
          client.get("/api/crew?activity=%20").status_code == 400)
    check("an absurd crew size is refused",
          client.get("/api/crew?activity=dinner&size=99").status_code == 400
          and client.get("/api/crew?activity=dinner&size=0").status_code == 400)
    check("markup typed into the activity stays text",
          client.get("/api/crew?activity=%3Cscript%3Ex%3C/script%3E+league")
          .json()["activity"] == "<script>x</script> league")
    check("control characters are stripped from the activity",
          "\n" not in client.get("/api/crew?activity=a%0Ab").json()["activity"])

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

    print("\nfifteen things found by reading the code:")
    from types import SimpleNamespace as _NS
    import app.importer as _imp15
    import app.vault as _vmod
    _t15 = Path(tempfile.mkdtemp(prefix="mg-fifteen-"))
    try:
        # Someone who imported only WhatsApp -- the import that needs no
        # permission -- was told they had no data, and could not save a name.
        _wv = Vault(_t15 / "v", MemoryKeyStore())
        _wv.write(rc, {"window_days": 2000, "messages": len(rc.messages)}, platform="whatsapp")
        check("a vault holding only WhatsApp counts as imported",
              _wv.any() and not _wv.exists())
        check("and is served as WhatsApp rather than as demo data",
              _wv.source() == "vault:whatsapp")
        _wv.seal("overrides.v1.enc", {"names": {"c_1": "Sam"}})
        check("names and paired phones can still be saved into it",
              _wv.unseal("overrides.v1.enc") == {"names": {"c_1": "Sam"}})
        check("and its own manifest is the one reported",
              (_wv.manifest() or {}).get("platform") == "whatsapp")
        check("nothing imported is still nothing", not Vault(_t15 / "empty", MemoryKeyStore()).any())

        # The hourly refresh re-read with the importer's default and turned
        # `mg import --days 2000` into 400 days an hour later.
        _asked15 = {}
        _was = (_imp15.run_whatsapp_import, _imp15.run_import, _vmod.Vault)
        try:
            _imp15.run_whatsapp_import = lambda days=400, **k: (
                _asked15.__setitem__("whatsapp", days) or _NS(ok=True, messages=1, note=""))
            _imp15.run_import = lambda days=400, **k: (
                _asked15.__setitem__("imessage", days) or _NS(ok=True, messages=1, note=""))
            _vmod.Vault = lambda *a, **k: _wv
            _rf.refresh_once()
        finally:
            _imp15.run_whatsapp_import, _imp15.run_import, _vmod.Vault = _was
        check("the hourly refresh reads back as far as you imported, not 400 days",
              _asked15 == {"whatsapp": 2000}, str(_asked15))

        # A store shaped like WhatsApp's, left in WAL mode with its newest rows
        # still in the log, the way it is while WhatsApp is running.
        from app.ingest.whatsapp import WhatsAppSource as _WA15, CORE_DATA_EPOCH as _CDE
        _wdb = _t15 / "ChatStorage.sqlite"
        _w = sqlite3.connect(_wdb)
        _w.execute("PRAGMA journal_mode=WAL")
        _w.execute("PRAGMA wal_autocheckpoint=0")
        _w.executescript("""
            CREATE TABLE ZWAPROFILEPUSHNAME (ZJID TEXT, ZPUSHNAME TEXT);
            CREATE TABLE ZWACHATSESSION (Z_PK INTEGER PRIMARY KEY, ZCONTACTJID TEXT,
                                         ZPARTNERNAME TEXT, ZSESSIONTYPE INTEGER);
            CREATE TABLE ZWAGROUPMEMBER (Z_PK INTEGER PRIMARY KEY, ZCHATSESSION INTEGER,
                                         ZMEMBERJID TEXT, ZCONTACTNAME TEXT, ZFIRSTNAME TEXT);
            CREATE TABLE ZWAMESSAGE (Z_PK INTEGER PRIMARY KEY, ZCHATSESSION INTEGER,
                                     ZISFROMME INTEGER, ZMESSAGEDATE REAL, ZTEXT TEXT,
                                     ZMESSAGETYPE INTEGER, ZGROUPMEMBER INTEGER,
                                     ZFROMJID TEXT, ZPUSHNAME TEXT);
            INSERT INTO ZWACHATSESSION VALUES (1, '120363@g.us', '', 1);
            INSERT INTO ZWAGROUPMEMBER VALUES (1, 1, '4479001@s.whatsapp.net', 'Ana', NULL);
            INSERT INTO ZWAGROUPMEMBER VALUES (2, 1, '4479002@s.whatsapp.net', 'Ben', NULL);
        """)
        _w.commit()
        _wnow = datetime.now().timestamp() - _CDE
        for _pk, _mem, _txt in ((1, 1, "dinner friday?"), (2, 2, "yes im in"),
                                (3, None, "who wrote this")):
            _w.execute("INSERT INTO ZWAMESSAGE VALUES (?, 1, 0, ?, ?, 0, ?, '120363@g.us', ?)",
                       (_pk, _wnow - 3600 + _pk, _txt, _mem, "Ana" if _mem == 1 else "Ben"))
            _w.commit()
        _in_log = (_t15 / "ChatStorage.sqlite-wal").stat().st_size > 0
        _tmpd = _t15 / "tmp"
        _tmpd.mkdir()
        # A temp copy cleans up after a clean run, so watch for it being made at
        # all: a killed import is the one that leaves it behind.
        _tmp_was, _mk_was, _cp_was = tempfile.tempdir, tempfile.mkdtemp, shutil.copy2
        _copied = []
        tempfile.tempdir = str(_tmpd)
        tempfile.mkdtemp = lambda *a, **k: (_copied.append("mkdtemp"), _mk_was(*a, **k))[1]
        shutil.copy2 = lambda *a, **k: (_copied.append(str(a[0])), _cp_was(*a, **k))[1]
        try:
            _wsrc = _WA15(path=str(_wdb), days=30)
            _wc = _wsrc.load()
        finally:
            tempfile.tempdir, tempfile.mkdtemp, shutil.copy2 = _tmp_was, _mk_was, _cp_was
            _w.close()
        _wby = {m.text: m.sender_id for m in _wc.messages}
        _wname = {c.id: c.name for c in _wc.contacts.values()}
        check("WhatsApp is read through its write-ahead log, newest messages included",
              _in_log and "yes im in" in _wby)
        check("and nothing of it is written to a temp folder on the way",
              not any(_tmpd.iterdir()) and not _copied, ", ".join(_copied))
        check("a group message is credited to the member who wrote it",
              _wname.get(_wby.get("dinner friday?")) == "Ana"
              and _wname.get(_wby.get("yes im in")) == "Ben")
        check("one with no member row is dropped, not handed to whoever is listed first",
              "who wrote this" not in _wby
              and _wsrc.report.dropped.get("unknown sender") == 1)
        check("a group with no name is not named after one of its members",
              [t.name for t in _wc.threads.values()] == ["Unknown"],
              str([t.name for t in _wc.threads.values()]))

        # WhatsApp histories are mostly international, and the phone pattern
        # knew one country.
        from app.privacy import redact as _red, redaction_hits as _hits
        _gone = {"+91 98765 43210": "[phone]", "+49 30 1234567": "[phone]",
                 "+44 7700 900123": "[phone]", "07700 900123": "[phone]",
                 "(415) 555-0199": "[phone]", "415-555-0134": "[phone]",
                 "GB82 WEST 1234 5698 7654 32": "[iban]", "DE89370400440532013000": "[iban]"}
        _leaks = {k: _red(f"it's {k} ok") for k, tok in _gone.items()
                  if _red(f"it's {k} ok") != f"it's {tok} ok"}
        check("phone numbers and bank accounts are hidden whatever country they are from",
              not _leaks, str(_leaks))
        _keep = ["on 2024-01-15 at 10.30", "it costs 1500", "see you at 7", "flat 12 3 4"]
        check("while dates, prices and times are left alone",
              all(_red(t) == t for t in _keep), str([_red(t) for t in _keep]))
        check("and what was hidden is reported once, not once per pattern it resembles",
              _hits("ring +44 7700 900123") == ["[phone]"], str(_hits("ring +44 7700 900123")))

        # Small threads built by hand, each the smallest case of one mistake.
        from app.models import (Contact as _Ct, Thread as _Th, Message as _Ms,
                                Corpus as _Cp, SELF_ID as _ME)

        def _mini(rows, group=False):
            cs = {c: _Ct(id=c, name=c.title(), handle=f"+1555000{i}")
                  for i, c in enumerate(("ana", "ben"))}
            th = _Th(id="t", name="Crew" if group else "Ana",
                     participant_ids=("ana", "ben") if group else ("ana",), is_group=group)
            ms = [_Ms(id=f"m{i}", thread_id="t", sender_id=who, ts=ts, text=x,
                      is_from_me=who == _ME) for i, (who, ts, x) in enumerate(rows)]
            return _Cp(contacts=cs, threads={"t": th}, messages=sorted(ms, key=lambda m: m.ts))

        _n15 = datetime(2026, 10, 14, 12)                  # a Wednesday
        _ago = lambda days, mins=0: _n15 - timedelta(days=days) + timedelta(minutes=mins)
        _pl = lambda c: [(p.state, p.assumed) for p in extract_plans(c, _n15)]

        check("in a group, one friend saying yes to another's plan confirms it",
              _pl(_mini([("ana", _ago(0, -60), "we should get dinner friday"),
                         ("ben", _ago(0, -55), "yes im in!")], group=True)) == [("confirmed", False)])
        check("but agreeing with yourself still does not",
              _pl(_mini([("ana", _ago(0, -60), "we should get dinner friday"),
                         ("ana", _ago(0, -55), "yes im in!")], group=True)) == [("proposed", False)])
        check("a stale plan mentioned again yesterday is not still stale",
              _pl(_mini([(_ME, _ago(20), "we should get coffee sometime"),
                         (_ME, _ago(1), "we should get coffee sometime")])) == [("floated", False)])
        check("one where both mentions are old stays stale",
              _pl(_mini([(_ME, _ago(40), "we should get coffee sometime"),
                         (_ME, _ago(25), "we should get coffee sometime")])) == [("stale", False)])
        check("a guessed outcome stays marked as a guess when mentions are merged",
              _pl(_mini([(_ME, _ago(30), "we should get dinner sometime"),
                         (_ME, _ago(10), "dinner on saturday?"),
                         ("ana", _ago(10, 5), "yes!")])) == [("done", True)])

        # How a one-on-one thread is described, on threads shaped to each mistake.
        def _prof(rows):
            p = profile_all(_mini(rows), _n15)[0]
            return p.state, p.trend
        _new40 = [(_ME if (d + k) % 2 else "ana", _n15 - timedelta(days=d, hours=k * 3),
                   "how's it going today") for d in range(40) for k in range(3)]
        check("a steady thread six weeks old is steady, not rising",
              _prof(_new40)[1] == "steady", str(_prof(_new40)))
        _parent = ([("ana", _n15 - timedelta(days=d, hours=h), "look at this photo of the garden")
                    for d in range(300) for h in (2, 9)]
                   + [(_ME, _n15 - timedelta(days=d, hours=1), "lovely!") for d in range(5, 300, 30)])
        check("600 messages from them and 10 from you is not a 'new' thread",
              _prof(_parent)[0] != "new", str(_prof(_parent)))
        _monthly = [(_ME if k % 2 else "ana", _n15 - timedelta(days=31 + 30 * m, minutes=10 * k),
                     "so good to catch up, tell me everything") for m in range(24) for k in range(30)]
        check("a friend you talk to monthly has not gone quiet after 31 days",
              _prof(_monthly)[0] != "quiet", str(_prof(_monthly)))
        _daily = [(_ME if k % 2 else "ana", _n15 - timedelta(days=35 + d, minutes=10 * k),
                   "what are you up to tonight") for d in range(200) for k in range(6)]
        check("one you talked to every day has, after 35",
              _prof(_daily)[0] == "quiet", str(_prof(_daily)))

        # Search, in the languages people actually text in.
        from app.search import search as _search
        _intl = _mini([("ana", _ago(3), "встреча в пятницу у метро"),
                       ("ana", _ago(2), "我们周六在東京见面吧"),
                       ("ana", _ago(1), "meet at Café Lumière after work"),
                       (_ME, _ago(1, 5), "see you at the cafe then")])
        def _found(q):
            return sorted(h.title for h in _search(_intl, [], [], q, _n15).hits
                          if h.kind == "message")
        check("a Russian question finds the Russian message",
              any("встреча" in d for d in _found("где встреча")), str(_found("где встреча")))
        check("a word in Japanese or Chinese is found inside the sentence around it",
              any("東京" in d for d in _found("東京")), str(_found("東京")))
        check("café and cafe find each other, whichever way round",
              len(_found("café")) == 2 and len(_found("cafe")) == 2, str(_found("café")))

        # The page. These read the script, as the checks on it above do; the
        # behaviour was also driven in a browser against the demo data.
        _js15 = Path("app/static/app.js").read_text()
        _api = _js15[_js15.index("const api = "):_js15.index("function signedOut")]
        check("a request the server refuses is an error, not an answer to read",
              "if (!r.ok)" in _api and "throw e" in _api)
        _now = _js15[_js15.index("function viewNow()"):_js15.index("function wireJumps()")]
        check("the drafts and thank-yous say so when they can't load, instead of 'Drafting…' for good",
              "drawDrafts().catch(" in _now and "drawThanks().catch(" in _now)
        _wd = _js15[_js15.index("function wireDraft("):_js15.index("// ----", _js15.index("function wireDraft("))]
        check("'Another version' comes back however its request ends",
              "finally" in _wd and "another.disabled = false" in _wd)
        check("and the name on a draft opens that person, as it looks like it should",
              "querySelector('.dwho[data-person]')" in _wd and ".who.link" not in _js15)
        _op = _js15[_js15.index("async function openPerson("):_js15.index("// ---------------------------------------------------------------- history")]
        _lh = _js15[_js15.index("async function loadHistory("):_js15.index("function renderHistory(")]
        check("a profile that arrives after you've opened someone else is dropped",
              _op.count("gen !== drawerGen") >= 2 and "drawerGen++" in _js15)
        check("and so is a page of their history, or one for a search you've typed past",
              "gen !== drawerGen || cid !== H.cid || q !== H.q" in _lh)
        _views = re.search(r"const VIEWS = \[([^\]]*)\]", _js15).group(1)
        check("Insights survives a reload and the Back button",
              "'insights'" in _views)
        #@fifteen
    finally:
        shutil.rmtree(_t15, ignore_errors=True)

    print("\npalette:")
    css = Path("app/static/styles.css").read_text()
    js = Path("app/static/app.js").read_text()
    themes = theme_tokens(css)
    check("styles.css defines both themes over the same token names",
          set(themes) == {"light", "dark"}
          and set(themes["light"]) == set(themes["dark"]),
          " / ".join(f"{k}: {len(v)} tokens" for k, v in themes.items()))

    # Text has to be legible on each of the four surfaces it can land on. The
    # theme this replaced put --faint at 2.97:1 on a panel, which is most of what
    # made the app read as murky. --sidebar is one of them: `aside` sets it, and
    # the nav, the counts and the footer all put neutral text on top.
    SURFACES = ("bg", "panel", "panel2", "sidebar")
    TEXT = ("ink", "dim", "faint")
    ACCENTS = ("accent", "adds", "functional", "neutral", "costs", "unclear", "stale", "plan")
    for name, t in sorted(themes.items()):
        worst = min((contrast_ratio(t[k], t[s_]), f"--{k} on --{s_}")
                    for k in TEXT for s_ in SURFACES)
        check(f"{name}: every neutral clears AA on every surface it lands on",
              worst[0] >= 4.5, f"worst {worst[1]} = {worst[0]:.2f}:1")
        worst = min((contrast_ratio(t[k], t[s_]), f"--{k} on --{s_}")
                    for k in ACCENTS for s_ in ("bg", "panel"))
        check(f"{name}: every accent clears AA as text",
              worst[0] >= 4.5, f"worst {worst[1]} = {worst[0]:.2f}:1")
        check(f"{name}: the outgoing bubble's own text and timestamp are legible",
              min(contrast_ratio(t["mine-ink"], t["mine"]),
                  contrast_ratio(t["mine-faint"], t["mine"])) >= 4.5,
              f"{contrast_ratio(t['mine-faint'], t['mine']):.2f}:1 on the timestamp")

        # The charts are drawn on --panel, and "you" and "them" must never collapse
        # into one another for a colour-blind reader.
        you, them = t["series-you"], t["series-them"]
        check(f"{name}: each chart series stands out from the chart surface",
              min(contrast_ratio(you, t["panel"]), contrast_ratio(them, t["panel"])) >= 3.0,
              f"you {contrast_ratio(you, t['panel']):.2f}:1, "
              f"them {contrast_ratio(them, t['panel']):.2f}:1")
        d = colour_distance(you, them)
        check(f"{name}: the two chart series stay apart under every kind of colour blindness",
              d >= 60, f"worst-case ΔE {d:.0f}")

    # The whole point of the tokens is that there is one place to change a colour.
    # A literal anywhere in the stylesheet's rules, or in the script that paints the
    # charts, is a colour that will not follow the theme. (The Mac app's status
    # screen and the locked page are deliberately outside this: they are served
    # before styles.css can be, and carry their own two neutrals.)
    body = css[css.index("*{box-sizing:border-box}"):]
    stray_css = set(re.findall(r"rgba?\([^)]*\)|#[0-9a-fA-F]{3,8}\b", body))
    stray_js = set(re.findall(r"#[0-9a-fA-F]{6}\b", js))
    check("styles.css and app.js write no colour outside the palette",
          not stray_css and not stray_js, ", ".join(sorted(stray_css | stray_js)))

    print()
    if FAILS:
        print(f"{len(FAILS)} FAILED: {', '.join(FAILS)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
