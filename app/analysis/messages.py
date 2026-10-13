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

import hashlib
from collections import Counter

import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from statistics import median

from ..models import Corpus, Plan, RelationshipProfile
from ..names import greeting_name, looks_like_handle, short_name
from .circles import REVIVE_WINDOW
from .phrasing import a_week, count, gap_words
from .proposals import propose
from .voice import (INTEREST_LINE, Style, check_in_parts, follow_up_options,
                    follow_up_parts, plan_parts,
                    reconnect_options,
                    reconnect_parts, reply_parts,
                    style_for, write)

_THING = (r"(job interview|interview|presentation|exam|finals?|big move|move|"
          r"trip|flight|first day|race|marathon|recital|audition|launch|wedding|birthday|"
          r"defense|show|gig|half marathon)")
# The article is the whole question of whose it is, so it gets captured too.
EVENT = re.compile(r"\b(my|your|ur|the|our|a|an)\s+" + _THING, re.I)
# Second person anywhere in the message: they are talking to me about it.
AT_ME = re.compile(r"\b(you|u|your|ur|yours|youre|you're)\b", re.I)


def whose(text: str) -> tuple[str, str] | None:
    """Who the thing in a message from them belongs to, if it can be told.

    "my interview" is theirs and "your interview" is mine, but "the interview"
    says nothing -- and the draft was reading every one of those as theirs.
    On this Mac's history that made one follow-up in two wrong: "how do u feel
    about the interview" is them asking about mine, and the app answered by
    telling me they had an interview I'd failed to ask about.
    """
    hit = EVENT.search(text)
    if not hit:
        return None
    article, thing = hit.group(1).lower(), hit.group(2).lower()
    if article == "my":
        return "theirs", thing
    if article in ("your", "ur"):
        return "mine", thing                  # they are asking me about mine
    if article == "our":
        return "ours", thing
    # No possessive. If the sentence is addressed to me it is probably mine;
    # otherwise they are narrating their own week.
    return ("mine" if AT_ME.search(text) else "theirs?"), thing


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


def _offset(did: str) -> int:
    """Stable per-draft starting point. blake2b rather than hash(), whose seed
    changes between runs -- the draft for someone must not move about."""
    return int.from_bytes(hashlib.blake2b(did.encode(), digest_size=4).digest(), "big")


# Enough mentions that "are you still running" is a fair thing to say. The
# interest map already drops anything not distinctive to this person and floors
# at 3; this asks for a little more before the draft states it as fact.
TOPIC_EVIDENCE = 4


def topic_lines(imap: dict[str, dict[str, int]]) -> dict[str, str]:
    """contact id -> one thing to ask them about, where there is one worth asking."""
    out = {}
    for cid, scores in (imap or {}).items():
        if not scores:
            continue
        label, hits = max(scores.items(), key=lambda kv: (kv[1], kv[0]))
        line = INTEREST_LINE.get(label)
        if line and hits >= TOPIC_EVIDENCE:
            out[cid] = line
    return out


def build_messages(corpus: Corpus, profiles: list[RelationshipProfile], plans: list[Plan],
                   now: datetime, seeds: dict[str, int] | None = None,
                   limit: int = 8, topics: dict[str, str] | None = None) -> list[Draft]:
    seeds = seeds or {}
    out: list[Draft] = []
    # How to draw a given draft again at a different index, so two that land on
    # the same wording can be pulled apart once we know which ones you will see.
    redraw: dict[str, tuple] = {}

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
            # The seed the page sends is a step: 0 on first sight, +1 each time
            # you ask for another version. Everyone therefore started on the
            # same option, and a hundred people got a hundred identical drafts.
            # Offsetting by the person means they start in different places and
            # "another version" still just moves one along.
            seed = seeds.get(did, 0)
            start = _offset(did) + seed
            # A draft you cannot tell the recipient of is worth less than one
            # you can: 7 of the 8 on this history were addressed to a bare
            # phone number. Nudged down, not dropped -- you may well know who
            # that number is, and naming them puts it straight back.
            if looks_like_handle(p.name):
                value *= 0.8
            redraw[did] = (lambda i, make=make, style=style, warm=warm:
                           write(make(i), style, warm=warm), start, variants)
            candidates.append(Draft(did, kind, p.name, [p.contact_id], thread.id, why,
                                    write(make(start % variants), style, warm=warm),
                                    round(value, 1),
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
            owner = whose(msg.text)
            if not owner:
                continue
            side, event = owner
            # Theirs to ask about, or nobody's. "How do u feel about the
            # interview" is them asking about mine, and a draft saying "how did
            # the interview go" sent back to them is the wrong way round.
            if side == "mine":
                continue
            # "How'd the interview go?" is a follow-up to "my job interview", so match
            # on the head noun, not the exact phrase they happened to use.
            key = event if event == "first day" else event.split()[-1].rstrip("s")
            if any(x.is_from_me and x.ts > msg.ts and key in x.text.lower() for x in dm):
                break
            days = (now - msg.ts).days
            # Only "my X" earns "their X". Without a possessive the app knows a
            # thing came up, not whose it was, and the line says only that.
            whosen = {"theirs": f"their {event}", "ours": f"the {event} you two mentioned",
                      "theirs?": f"a {event}"}[side]
            # Only "my X" lets the draft say "your X" back.
            sure = side == "theirs"
            add("follow_up", f"{first} mentioned {whosen} {count(days, 'day')} ago, and you haven't "
                "asked about it since.",
                lambda s, event=event, sure=sure: follow_up_parts(style, name, event, s, sure),
                follow_up_options(sure),
                # A guess about whose it is is worth less than knowing.
                (58 if side != "theirs?" else 50) + 0.32 * p.importance,
                detail=msg.text[:180])
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
            gap = gap_words(age)
            topic = (topics or {}).get(p.contact_id, "")
            add("reconnect", f"You used to talk about {a_week(p.msgs_per_week)}. "
                f"It's been {count(age, 'day')}.",
                lambda s: reconnect_parts(style, name, s, gap, topic),
                reconnect_options(gap, topic),
                # Something only true of them beats something true of anyone.
                (52 if topic else 46) + 0.3 * p.warmth + min(15, age / 12), warm=True)
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
    # Pull duplicates apart across every candidate, then choose: a duplicate
    # that cannot be reworded is dropped, and the next-best draft takes the
    # slot instead of the page showing the same words twice.
    return _mix(_spread(out, redraw), limit)


# Five of the eight drafts were reconnects. Varying the wording stopped them
# reading as one message; this stops them reading as one idea. A list that
# offers four different reasons to write is worth more than the eight
# best-scoring instances of the same reason.
MAX_PER_KIND = 3


def _mix(drafts: list[Draft], limit: int) -> list[Draft]:
    """The best drafts, without letting any one reason crowd out the rest."""
    picked, held, seen = [], [], Counter()
    for d in drafts:
        if len(picked) == limit:
            break
        if seen[d.kind] < MAX_PER_KIND:
            picked.append(d)
            seen[d.kind] += 1
        else:
            held.append(d)
    # Better a fourth reconnect than an empty slot, if nothing else is left.
    for d in held:
        if len(picked) == limit:
            break
        picked.append(d)
    picked.sort(key=lambda d: d.value, reverse=True)
    return picked


def _body(text: str) -> str:
    """The draft without its greeting -- the greeting carries the name, so two
    drafts can read identically underneath and still differ on the first line."""
    rest = text.split("\n")[1:]
    return "\n".join(rest) if rest else text


def _spread(drafts: list[Draft], redraw: dict[str, tuple]) -> list[Draft]:
    """No two drafts saying the same thing, in the same words.

    Offsetting each person spreads them across the pool, but with more people
    than wordings some still collide, and a pair that collides often lands side
    by side. A repeat is drawn again further along its own pool until it is not
    one -- and a repeat that cannot be redrawn is dropped rather than shown
    twice. The first version of this skipped those instead of dropping them,
    which left a plan and a group plan offering word-for-word the same message.
    """
    seen: set[str] = set()
    kept: list[Draft] = []
    for d in drafts:
        body = _body(d.draft)
        if body not in seen:
            seen.add(body)
            kept.append(d)
            continue
        render, start, variants = redraw.get(d.id, (None, 0, 0))
        fixed = None
        for step in range(1, variants):
            text = render((start + step) % variants)
            if _body(text) not in seen:
                fixed = replace(d, draft=text)
                break
        if fixed is None:
            continue                      # nothing else it can say; let it go
        seen.add(_body(fixed.draft))
        kept.append(fixed)
    return kept
