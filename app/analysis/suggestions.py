"""Turning observations into small, concrete, optional things you could do.

This module is the reason the rest of the analysis exists. Noticing that a thread
has gone quiet is not useful on its own; noticing it and saying "you could send
three words to Marcus today" is.

Three rules the copy here follows:

1. Every suggestion is something you *could* do, never something you should. The
   app has no idea what your week is like.
2. Good news counts. An app that only ever surfaces problems teaches you to read
   your friendships as a list of problems, which is both miserable and inaccurate.
3. Never attribute motive. "It's been 37 days" is an observation. "They've been
   distant" is a story, and it is usually the wrong one.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime

from ..models import Plan, RelationshipProfile
from ..names import looks_like_handle, short_name
from .phrasing import a_week, count, rate


@dataclass
class Suggestion:
    kind: str
    who: str
    target_id: str
    observation: str      # what is actually in the data
    action: str           # one small concrete thing, phrased as an option
    weight: float
    tone: str             # warm | nudge | reflect


def _days(now: datetime, then: datetime | None) -> int:
    return (now - then).days if then else 0


MAX_PER_KIND = 3


def build(profiles: list[RelationshipProfile], plans: list[Plan],
          now: datetime | None = None, limit: int = 8,
          snoozed_people: set[str] | frozenset = frozenset()) -> list[Suggestion]:
    """`snoozed_people`: contact ids you've snoozed, who get no "it's gone quiet"
    observations until the snooze runs out. Snoozed plans are the caller's to
    leave out of `plans`."""
    now = now or datetime.now()
    out: list[Suggestion] = []

    for p in profiles:
        first = short_name(p.name)
        gap = _days(now, p.last_contact)
        asleep = p.contact_id in snoozed_people

        if p.state == "quiet" and p.msgs_per_week > 1.2 and not asleep:
            out.append(Suggestion(
                "reconnect", p.name, p.contact_id,
                f"You used to talk about {a_week(p.msgs_per_week)}. "
                f"It's been {count(gap, 'day')}.",
                f"No occasion needed — “hey, you crossed my mind” does the whole job.",
                weight=88 + p.warmth / 5, tone="warm"))

        elif p.state == "uneven":
            # Cite whatever is actually driving it. Naming a generic imbalance when
            # the real signal is something else reads as a grievance being invented.
            worst = min(p.signals, key=lambda s: s.contribution) if p.signals else None
            detail = f"{worst.label.lower()}: {worst.display}" if worst else "the split"
            if worst is not None and worst.key == "self_editing":
                # The signal is about how the user writes, so the suggestion has to
                # be as well. Turning it into a remark about the friend's schedule
                # would be both inaccurate and a little unfair to them.
                out.append(Suggestion(
                    "reflect", p.name, p.contact_id,
                    f"You write to {first} far more carefully than you write to anyone "
                    f"else — {worst.display}.",
                    f"That can be ordinary: some people we're newer with, or more "
                    f"formal around, and that's fine. It's only worth sitting with if "
                    f"the number is higher than you'd have guessed.",
                    weight=74, tone="reflect"))
            else:
                out.append(Suggestion(
                    "uneven", p.name, p.contact_id,
                    f"The thread's running a little unevenly at the moment — {detail}.",
                    f"Nothing needs fixing today, and a quarter like this often just "
                    f"means {first} has a lot on. If it's been sitting with you, saying "
                    f"so plainly usually goes better than waiting for it to even out.",
                    weight=74, tone="reflect"))

        elif p.state == "close" and p.trend == "rising":
            age = _days(now, p.first_contact)
            fresh = age < 180
            out.append(Suggestion(
                "growing", p.name, p.contact_id,
                (f"Six months in and already {rate(p.msgs_per_week_recent, 'message')} a week, "
                 f"warm both ways." if fresh else
                 f"This one's picked up lately — {rate(p.msgs_per_week_recent, 'message')} a "
                 f"week, warm both ways."),
                (f"Worth noticing. A new friendship taking root like this is rarer than "
                 f"it feels at the time." if fresh else
                 f"Nothing to do here. It's just going well, which is worth seeing "
                 f"written down."),
                weight=70, tone="warm"))

        elif p.state == "close" and gap > 12 and not asleep:
            out.append(Suggestion(
                "check_in", p.name, p.contact_id,
                f"One of your closer threads, quiet for {count(gap, 'day')}.",
                f"Not a problem — just the kind of gap that's easy to close while "
                f"it's still small.",
                weight=62, tone="warm"))

        if p.self_editing > 70 and p.importance > 45:
            out.append(Suggestion(
                "reflect", p.name, p.contact_id,
                f"You write more carefully to {first} than you do to most people "
                f"({p.self_editing:.0f} against your baseline of 33).",
                f"That's often just context — colleagues and newer friends get more "
                f"care from most of us. Only worth a thought if the number surprises you.",
                weight=56, tone="reflect"))

    # Plans people agreed to and never dated. The friendliest thing the app does:
    # these are almost always wanted by both sides and stalled on nobody wanting
    # to be the one to pick a day.
    stale = sorted((p for p in plans if p.state == "stale"),
                   key=lambda p: p.last_activity, reverse=True)[:4]
    for pl in stale:
        idle = _days(now, pl.last_activity)
        what = pl.title.split(" with ")[0].lower()
        out.append(Suggestion(
            "revive_plan", pl.thread_name, pl.id,
            f"You both wanted to do {what} — it came up {count(idle, 'day')} ago and never "
            f"got a date.",
            f"Naming a specific day is usually the whole blocker. “{what.capitalize()} "
            f"next week?” is enough.",
            weight=66, tone="nudge"))

    out.sort(key=lambda s: s.weight, reverse=True)

    # An observation about someone the app can only call "(415) 555-0199" is
    # worth less than one about a name: 7 of the 8 here were the former. Nudged
    # down rather than dropped, and naming them puts it straight back.
    out = [replace(s, weight=s.weight * 0.8) if looks_like_handle(s.who) else s
           for s in out]
    out.sort(key=lambda s: s.weight, reverse=True)

    # Don't pile several items onto one person -- a list that mentions the same
    # friend four times reads as a case being built against them.
    seen: dict[str, int] = {}
    kept: list[Suggestion] = []
    for s in out:
        n = seen.get(s.who, 0)
        if n >= 1:
            continue
        seen[s.who] = n + 1
        kept.append(s)
    # Nor should one kind of observation fill the panel. Every one of the eight
    # was a "reconnect", which reads as a single complaint repeated rather than
    # a handful of different things worth noticing.
    picked, held, by_kind = [], [], Counter()
    for s in kept:
        if by_kind[s.kind] < MAX_PER_KIND:
            picked.append(s)
            by_kind[s.kind] += 1
        else:
            held.append(s)
    picked = (picked + held)[:limit]
    picked.sort(key=lambda s: s.weight, reverse=True)
    return picked
