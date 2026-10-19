"""The whole history at once, rather than one person at a time.

Everything else in this app is about somebody: a draft to send, a plan to
pick a date for, a thread that has gone quiet. This is the other question --
what does the shape of all of it look like -- and until now the only piece of
it anywhere was a messaging-rate chart sitting oddly on the People page.

Nothing here is a score. They are facts about a year of texting: when you
write, who you write to, how much of it is you talking, and how your own
messages read. All computed here, on this machine, like the rest.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime

from .models import Corpus, RelationshipProfile

DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
TOP_PEOPLE = 8


def _part_of_day(hour: int) -> str:
    if hour < 5:
        return "late night"
    if hour < 12:
        return "morning"
    if hour < 17:
        return "afternoon"
    if hour < 22:
        return "evening"
    return "late night"


def build(corpus: Corpus, profiles: list[RelationshipProfile],
          now: datetime | None = None) -> dict:
    now = now or datetime.now()
    msgs = corpus.messages
    if not msgs:
        return {"empty": True}

    mine = sum(1 for m in msgs if m.is_from_me)
    theirs = len(msgs) - mine
    by_hour = Counter(m.ts.hour for m in msgs)
    by_day = Counter(m.ts.weekday() for m in msgs)
    group_ids = {t.id for t in corpus.threads.values() if t.is_group}
    in_groups = sum(1 for m in msgs if m.thread_id in group_ids)

    # Who you actually talk to, by how much of your year they are.
    ranked = sorted(profiles, key=lambda p: p.total_messages, reverse=True)[:TOP_PEOPLE]
    biggest = max((p.total_messages for p in ranked), default=1) or 1

    busiest_hour = max(by_hour, key=by_hour.get) if by_hour else 0
    busiest_day = max(by_day, key=by_day.get) if by_day else 0
    first, last = corpus.span
    days = max(1, (last - first).days)

    return {
        "empty": False,
        "messages": len(msgs),
        "people": len(corpus.contacts),
        "threads": len(corpus.threads),
        "days": days,
        "per_day": round(len(msgs) / days, 1),
        # Who is doing the talking, over everything rather than one thread.
        "mine": mine,
        "theirs": theirs,
        "mine_share": round(100 * mine / len(msgs)),
        "group_share": round(100 * in_groups / len(msgs)),
        "busiest_hour": busiest_hour,
        "busiest_part": _part_of_day(busiest_hour),
        "busiest_day": DAYS[busiest_day],
        "by_hour": [by_hour.get(h, 0) for h in range(24)],
        "by_day": [by_day.get(d, 0) for d in range(7)],
        "top": [{"id": p.contact_id, "name": p.name, "messages": p.total_messages,
                 "share": round(100 * p.total_messages / biggest),
                 "state": p.state} for p in ranked],
        # A year is long enough that the first month and the last are
        # different places; saying so is more useful than one average.
        "first_month": first.strftime("%B %Y"),
        "last_month": last.strftime("%B %Y"),
    }
