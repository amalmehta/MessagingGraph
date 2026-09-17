"""Draft a proposed plan for a group of connected people.

The proposal comes from the group's own history, in this order of preference:

1. **Revive** something they already wanted: a plan that came up recently and
   never got a date. Everyone said yes once; it usually just needs a day.
2. **Repeat** what they've actually done together most.
3. **Start fresh** with a simple default when there's no history.

The day is the weekday that has worked for this group before, far enough out
that people can reply. Everything is computed locally, and the result is a draft
for you to read, edit and send yourself. Nothing here can send a message.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..models import Corpus, Plan
from ..names import greeting_name, group_label, short_name
from .circles import REVIVE_WINDOW, activity_of
from .voice import plan_parts, style_for, write

LANDED = {"done", "confirmed", "scheduled"}
DAYTIME = {"brunch": 11, "breakfast": 9, "coffee": 10, "lunch": 12, "hike": 9, "hiking": 9,
           "farmers market": 10, "market": 10, "picnic": 13, "museum": 14, "bike ride": 10,
           "walk": 10, "run": 8, "beach": 12, "pottery class": 11, "pottery": 11}
FRESH = ["dinner", "drinks", "brunch", "walk"]
# How each activity reads mid-sentence: "a hike", "the farmers market", "drinks".
PHRASE = {"hike": "a hike", "hiking": "a hike", "climbing gym": "the climbing gym", "gym": "the gym",
          "movie": "a movie", "film": "a film", "farmers market": "the farmers market",
          "market": "the market", "pottery class": "the pottery class", "picnic": "a picnic",
          "museum": "the museum", "show": "the show", "concert": "the concert", "gig": "the gig",
          "bike ride": "a bike ride", "trip": "the trip", "cabin": "the cabin trip",
          "party": "the party", "catch up": "a catch-up", "hang out": "a hang", "hang": "a hang",
          "visit": "a visit", "call": "a call", "walk": "a walk", "run": "a run",
          "beach": "the beach", "birthday": "the birthday plans"}
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


@dataclass
class Proposal:
    thread_id: str | None
    group_name: str
    member_ids: list[str]
    member_names: list[str]
    activity: str
    basis: str                      # revive | repeat | fresh
    when: datetime
    when_label: str
    reasons: list[str]
    draft: str
    alternatives: list[str] = field(default_factory=list)
    seed: int = 0
    options: int = 1


def _time_label(hour: int) -> str:
    if hour == 12:
        return "noon"
    return f"{hour % 12 or 12}{'am' if hour < 12 else 'pm'}"


def _day_phrase(when: datetime, now: datetime) -> str:
    days = (when.date() - now.date()).days
    name = WEEKDAYS[when.weekday()]
    return f"this {name}" if days <= 6 else f"{name} the {when.day}{_suffix(when.day)}"


def _suffix(d: int) -> str:
    return "th" if 11 <= d % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(d % 10, "th")


def propose(corpus: Corpus, plans: list[Plan], member_ids: list[str],
            thread_id: str | None = None, seed: int = 0,
            now: datetime | None = None) -> Proposal:
    now = now or datetime.now()
    members = [c for c in member_ids if c in corpus.contacts]
    if not members:
        raise ValueError("choose at least one person")
    thread = corpus.threads.get(thread_id) if thread_id else None
    chosen = set(members)
    solo = len(members) == 1
    them = short_name(corpus.contacts[members[0]].name) if solo else "this group"

    relevant = [p for p in plans
                if (thread and p.thread_id == thread.id)
                or chosen <= set(p.participant_ids)]

    candidates: list[tuple[str, str, str]] = []           # (activity, basis, reason)
    seen: set[str] = set()
    # Only revive what was wanted *in this conversation*: a one-on-one draft to Rafa
    # shouldn't bring up the Dinner Club's plan as though it were just the two of you.
    revive_pool = [p for p in relevant if not thread or p.thread_id == thread.id]
    for p in sorted((p for p in revive_pool if p.state == "stale"
                     and now - p.last_activity <= REVIVE_WINDOW),
                    key=lambda p: p.last_activity, reverse=True):
        a = activity_of(p).lower()
        if a not in seen:
            seen.add(a)
            candidates.append((a, "revive", f"{a.capitalize()} came up {(now - p.last_activity).days} "
                               "days ago and never got a date. " + ("You both wanted it once." if solo
                                                    else "Everyone wanted it once.")))
    landed = Counter(activity_of(p).lower() for p in relevant if p.state in LANDED)
    for a, n in landed.most_common():
        if a not in seen:
            seen.add(a)
            candidates.append((a, "repeat", f"{a.capitalize()} is what you've actually done together "
                               f"most ({n} time{'s' if n != 1 else ''})."))
    for a in FRESH:
        if a not in seen and len(candidates) < 3:
            seen.add(a)
            candidates.append((a, "fresh", "No plan history here yet, so this is a simple place to start."))

    activity, basis, reason = candidates[seed % len(candidates)]

    worked = Counter(p.when_date.weekday() for p in relevant
                     if p.state in LANDED and p.when_date)
    if worked:
        weekday, hits = worked.most_common(1)[0]
        total = sum(worked.values())
        day_reason = (f"{WEEKDAYS[weekday]}s have worked {'with ' + them if solo else 'for this group'} before "
                      f"({hits} of {total} plans that happened).")
    else:
        weekday, day_reason = 5, "Saturday, since there's no record yet of which days work."
    ahead = (weekday - now.weekday()) % 7
    if ahead < 3:
        ahead += 7                                   # leave time for people to reply
    hour = DAYTIME.get(activity, 19)
    when = (now + timedelta(days=ahead)).replace(hour=hour, minute=0, second=0, microsecond=0)

    names = [corpus.contacts[c].name for c in members]
    firsts = [short_name(n) for n in names]
    day, time = _day_phrase(when, now), _time_label(hour)
    solo = len(members) == 1
    style = style_for(corpus, now, members[0] if solo else None)
    who = greeting_name(names[0]) if solo else ("you two" if len(members) == 2 else None)
    draft = write(plan_parts(style, activity, when, now, hour, who, not solo, basis == "revive",
                             seed // len(candidates)), style)

    reasons = [reason, day_reason]
    if thread and thread.is_group:
        reasons.append(f"Everyone here is in {thread.name}, so a message there reaches them all.")

    return Proposal(
        thread_id=thread.id if thread else None,
        group_name=thread.name if thread else group_label(names),
        member_ids=members, member_names=names, activity=activity, basis=basis,
        when=when, when_label=f"{WEEKDAYS[when.weekday()]}, {when.strftime('%b')} {when.day} · {time}",
        reasons=reasons, draft=draft,
        alternatives=[c[0] for c in candidates if c[0] != activity][:3],
        seed=seed, options=len(candidates) * 3,
    )
