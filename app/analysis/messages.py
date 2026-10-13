"""Messages worth sending: concrete drafts for specific people, ranked by value.

Each draft answers "why this person, why now" from history, and is written in your
own voice -- learned from what you've actually been sending lately (see voice.py).
Value ranks what would mean most to the other person: someone waiting on a reply
first, then following up on something they told you, then plans, then reconnecting.

Nothing here sends anything. Every draft is for you to copy, edit or skip.

Deliberately left out of automatic follow-ups: health, medical appointments, grief
and money. Those deserve a message you write yourself, not a template that guessed
from a keyword.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from statistics import median

from ..models import Corpus, Plan, RelationshipProfile
from ..names import greeting_name, short_name
from .circles import REVIVE_WINDOW
from .phrasing import a_week, count
from .proposals import propose
from .voice import (Style, check_in_parts, follow_up_parts, plan_parts, reconnect_parts, reply_parts,
                    style_for, write)

EVENT = re.compile(
    r"\b(?:my|the|our|a|an)\s+(job interview|interview|presentation|exam|finals?|big move|move|"
    r"trip|flight|first day|race|marathon|recital|audition|launch|wedding|birthday|defense|"
    r"show|gig|half marathon)\b", re.I)


@dataclass
class Draft:
    id: str
    kind: str              # reply | follow_up | revive_plan | group_plan | reconnect | check_in
    who: str
    target_ids: list[str]
    thread_id: str | None
    why: str
    draft: str
    value: float
    detail: str = ""
    seed: int = 0
    options: int = 1
    voice: dict = field(default_factory=dict)


def _voice_note(style: Style) -> dict:
    return {"window_days": style.window_days, "traits": style.describe()}


def build_messages(corpus: Corpus, profiles: list[RelationshipProfile], plans: list[Plan],
                   now: datetime, seeds: dict[str, int] | None = None,
                   limit: int = 8) -> list[Draft]:
    seeds = seeds or {}
    out: list[Draft] = []

    for p in profiles:
        if p.state == "new":
            continue
        thread = corpus.direct_thread(p.contact_id)
        dm = corpus.messages_in(thread.id) if thread else []
        if not dm:
            continue
        first = short_name(p.name)
        name = greeting_name(p.name)                    # None for someone saved only as a number
        style = style_for(corpus, now, p.contact_id)
        candidates: list[Draft] = []

        def add(kind, why, make, variants, value, detail="", warm=False):
            did = f"{kind}:{p.contact_id}"
            seed = seeds.get(did, 0)
            candidates.append(Draft(did, kind, p.name, [p.contact_id], thread.id, why,
                                    write(make(seed % variants), style, warm=warm), round(value, 1),
                                    detail, seed, variants, _voice_note(style)))

        last = dm[-1]
        age = (now - last.ts).days
        if not last.is_from_me and "?" in last.text and 1 <= age <= 14:
            add("reply", f"{first} asked you something {age} day{'s' if age != 1 else ''} ago, "
                "and it's still the last message in your thread.",
                lambda s: reply_parts(style, name, s), 2, 62 + 0.38 * p.importance, detail=last.text[:180])

        cut = now - timedelta(days=45)
        for msg in reversed(dm):
            if msg.ts < cut or msg.is_from_me or (now - msg.ts).days < 3:
                continue
            hit = EVENT.search(msg.text)
            if not hit:
                continue
            event = hit.group(1).lower()
            # "How'd the interview go?" is a follow-up to "my job interview", so match
            # on the head noun, not the exact phrase they happened to use.
            key = event if event == "first day" else event.split()[-1].rstrip("s")
            if any(x.is_from_me and x.ts > msg.ts and key in x.text.lower() for x in dm):
                break
            days = (now - msg.ts).days
            add("follow_up", f"{first} mentioned their {event} {count(days, 'day')} ago, and you haven't "
                "asked about it since.",
                lambda s, event=event: follow_up_parts(style, name, event, s), 2,
                58 + 0.32 * p.importance, detail=msg.text[:180])
            break

        stale = [pl for pl in plans if pl.state == "stale" and pl.thread_id == thread.id
                 and now - pl.last_activity <= timedelta(days=45)]
        if stale:
            # The seed chooses the activity and the day, not just the wording, so it has to
            # reach propose(): "another version" should be able to offer ramen on Tuesday,
            # not only reword the same dinner.
            did = f"revive_plan:{p.contact_id}"
            prop = propose(corpus, plans, [p.contact_id], thread.id, seeds.get(did, 0), now)
            add("revive_plan", prop.reasons[0] + " " + prop.reasons[1],
                lambda _: plan_parts(style, prop.activity, prop.when, now, prop.when.hour, name, False,
                                     prop.basis == "revive", prop.phrasing), prop.options,
                50 + 0.35 * p.importance, detail=prop.when_label)

        if p.state == "quiet" and p.msgs_per_week > 1.2:
            add("reconnect", f"You used to talk about {a_week(p.msgs_per_week)}. "
                f"It's been {count(age, 'day')}.",
                lambda s: reconnect_parts(style, name, s), 3,
                46 + 0.3 * p.warmth + min(15, age / 12), warm=True)
        elif p.state in ("close", "steady"):
            opens = [c for i, c in enumerate(dm) if i == 0 or c.ts - dm[i - 1].ts > timedelta(hours=6)]
            gaps = [(b.ts - a.ts).days for a, b in zip(opens, opens[1:])]
            usual = median(gaps) if gaps else 7
            if age >= max(10, 2.5 * usual):
                add("check_in", f"One of your closer threads, and it's been {count(age, 'day')} "
                    f"when you'd usually talk every {max(1, round(usual))}.",
                    lambda s: check_in_parts(style, name, s), 3, 40 + 0.35 * p.importance)

        if candidates:
            out.append(max(candidates, key=lambda d: d.value))

    # Group plans: the groups where something was wanted recently and never dated.
    group_drafts = []
    importance = {p.contact_id: p.importance for p in profiles}
    group_style = style_for(corpus, now)
    for t in corpus.threads.values():
        if not t.is_group:
            continue
        recent = [pl for pl in plans if pl.thread_id == t.id and pl.state == "stale"
                  and now - pl.last_activity <= REVIVE_WINDOW]
        members = [c for c in t.participant_ids if c in corpus.contacts]
        if not recent or len(members) < 2:
            continue
        did = f"group_plan:{t.id}"
        seed = seeds.get(did, 0)
        prop = propose(corpus, plans, members, t.id, seed, now)
        text = write(plan_parts(group_style, prop.activity, prop.when, now, prop.when.hour,
                                "you two" if len(members) == 2 else None, True, prop.basis == "revive",
                                prop.phrasing), group_style)
        avg = sum(importance.get(c, 0) for c in members) / len(members)
        group_drafts.append(Draft(did, "group_plan", t.name, members, t.id, " ".join(prop.reasons[:2]), text,
                                  round(48 + 0.3 * avg, 1), prop.when_label, seed, prop.options,
                                  _voice_note(group_style)))
    group_drafts.sort(key=lambda d: d.value, reverse=True)
    out += group_drafts[:2]

    out.sort(key=lambda d: d.value, reverse=True)
    return out[:limit]
