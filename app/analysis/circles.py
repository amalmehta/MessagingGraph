"""Who's connected to whom, and the plans between them.

Connection here means something the data actually shows: two people are
connected if they're in a group chat together. Nothing is inferred from names,
timing or guesswork -- if the app says Priya and Rafa are connected, you can open
the chat that proves it.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta

from ..models import Corpus, Plan

CONFIRMED = {"confirmed", "scheduled"}
IN_MOTION = {"floated", "proposed"}
# A plan that stalled a year ago isn't "worth reviving" in any useful sense.
# Past this it's reported as lapsed: counted, but not put in front of you.
REVIVE_WINDOW = timedelta(days=90)


def activity_of(plan: Plan) -> str:
    return plan.title.split(" with ")[0]


def plan_row(p: Plan, corpus: Corpus, now: datetime) -> dict:
    thread = corpus.threads.get(p.thread_id)
    ids = [c for c in p.participant_ids if c in corpus.contacts]
    return {
        "id": p.id, "activity": activity_of(p), "state": p.state,
        "thread_id": p.thread_id, "thread_name": p.thread_name,
        "is_group": bool(thread and thread.is_group),
        "person_ids": ids, "people": [corpus.contacts[c].name for c in ids],
        "when_text": p.when_text, "when_date": p.when_date,
        "days_idle": (now - p.last_activity).days, "confidence": p.confidence,
    }


def plans_overview(corpus: Corpus, plans: list[Plan], now: datetime) -> dict:
    confirmed = sorted((p for p in plans if p.state in CONFIRMED),
                       key=lambda p: p.when_date or p.last_activity)
    in_motion = sorted((p for p in plans if p.state in IN_MOTION),
                       key=lambda p: p.last_activity, reverse=True)
    stale = [p for p in plans if p.state == "stale"]
    revive = sorted((p for p in stale if now - p.last_activity <= REVIVE_WINDOW),
                    key=lambda p: p.last_activity, reverse=True)

    groups: dict[str, dict] = {}
    for p in [*confirmed, *in_motion, *revive]:
        thread = corpus.threads.get(p.thread_id)
        if not (thread and thread.is_group):
            continue
        g = groups.setdefault(p.thread_id, {
            "thread_id": p.thread_id, "name": thread.name,
            "members": [corpus.contacts[c].name for c in thread.participant_ids
                        if c in corpus.contacts],
            "plans": [], "last": p.last_activity,
        })
        g["plans"].append(plan_row(p, corpus, now))
        g["last"] = max(g["last"], p.last_activity)

    return {
        "confirmed": [plan_row(p, corpus, now) for p in confirmed],
        "in_motion": [plan_row(p, corpus, now) for p in in_motion],
        "revive": [plan_row(p, corpus, now) for p in revive],
        "groups": sorted(groups.values(), key=lambda g: g["last"], reverse=True),
        "counts": {"confirmed": len(confirmed), "in_motion": len(in_motion),
                   "revive": len(revive), "lapsed": len(stale) - len(revive),
                   "done": sum(p.state == "done" for p in plans),
                   "dropped": sum(p.state == "dropped" for p in plans)},
        "revive_window_days": REVIVE_WINDOW.days,
    }


def connections(corpus: Corpus, plans: list[Plan], now: datetime) -> dict:
    msgs = Counter(m.thread_id for m in corpus.messages)
    last: dict[str, datetime] = {}
    for m in corpus.messages:
        last[m.thread_id] = m.ts

    circles, seen_sets = [], set()
    via: dict[tuple[str, str], list[str]] = defaultdict(list)
    for t in sorted((t for t in corpus.threads.values() if t.is_group),
                    key=lambda t: last.get(t.id, datetime.min), reverse=True):
        members = sorted(c for c in t.participant_ids if c in corpus.contacts)
        if len(members) < 2:
            continue
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                via[(a, b)].append(t.name)
        key = tuple(members)
        if key in seen_sets:
            continue
        seen_sets.add(key)
        thread_plans = [p for p in plans if p.thread_id == t.id]
        circles.append({
            "thread_id": t.id, "name": t.name,
            "members": [{"id": c, "name": corpus.contacts[c].name} for c in members],
            "messages": msgs.get(t.id, 0), "last_active": last.get(t.id),
            "days_idle": (now - last[t.id]).days if t.id in last else None,
            "plans_done": sum(p.state == "done" for p in thread_plans),
            "plans_open": sum(p.state in CONFIRMED | IN_MOTION for p in thread_plans),
        })

    by_contact: dict[str, list[dict]] = defaultdict(list)
    for (a, b), names in via.items():
        by_contact[a].append({"id": b, "name": corpus.contacts[b].name, "via": names})
        by_contact[b].append({"id": a, "name": corpus.contacts[a].name, "via": names})
    for lst in by_contact.values():
        lst.sort(key=lambda x: (-len(x["via"]), x["name"]))

    solo = sorted((c.name for cid, c in corpus.contacts.items() if cid not in by_contact),
                  key=str.lower)
    return {"circles": circles, "by_contact": dict(by_contact), "solo": solo,
            "pairs": len(via)}
