"""Messages worth sending: concrete drafts for specific people, ranked by value.

Each draft answers "why this person, why now" from history, and is written in
the user's own texting voice with that person -- their usual greeting, whether
they write in lowercase, use exclamation marks or emoji. Value ranks what would
mean most to the other person: someone waiting on a reply first, then following
up on something they told you, then plans, then reconnecting.

Nothing here sends anything. Every draft is for the user to copy, edit or skip.

Deliberately left out of automatic follow-ups: health, medical appointments,
grief and money. Those deserve a message you write yourself, not a template that
guessed from a keyword.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from statistics import median

from ..models import Corpus, Message, Plan, RelationshipProfile
from ..names import greeting_name, short_name
from .circles import REVIVE_WINDOW
from .proposals import propose

GREETINGS = ("hey", "heyy", "heyyy", "hi", "hii", "hiya", "yo", "hello")   # not "morning": drafts go out any time
EMOJI = ["🙂", "😊", "❤️", "🥰", "😂", "🫶", "💛", "✨", "🙏", "😅"]
EVENT = re.compile(
    r"\b(?:my|the|our|a|an)\s+(job interview|interview|presentation|exam|finals?|big move|move|"
    r"trip|flight|first day|race|marathon|recital|audition|launch|wedding|birthday|defense|"
    r"show|gig|half marathon)\b", re.I)


@dataclass
class Voice:
    greeting: str = "hey"
    lowercase: bool = False
    exclaim: bool = True
    emoji: str = ""


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


def voice_of(mine: list[Message]) -> Voice:
    texts = [m.text for m in mine if m.text][-250:]
    if len(texts) < 8:
        return Voice()
    firsts = Counter(t.split()[0].lower().strip(",.!?") for t in texts if t.split())
    greeting = next((w for w, _ in firsts.most_common() if w in GREETINGS), "hey")
    starts = [t.lstrip()[0] for t in texts if t.lstrip() and t.lstrip()[0].isalpha()]
    lowercase = bool(starts) and sum(ch.islower() for ch in starts) / len(starts) > 0.6
    exclaim = sum("!" in t for t in texts) / len(texts) > 0.2
    used = Counter(e for t in texts for e in EMOJI if e in t)
    share = sum(1 for t in texts if any(e in t for e in EMOJI)) / len(texts)
    emoji = used.most_common(1)[0][0] if used and share > 0.15 and used.most_common(1)[0][0] != "😂" else ""
    return Voice(greeting, lowercase, exclaim, emoji)


def restyle(text: str, v: Voice) -> str:
    """Put a neutral draft into the user's own register with this person."""
    if text.lower().startswith("hey"):
        text = v.greeting.capitalize() + text[3:]
    if not v.exclaim:
        text = re.sub(r"^([^!?.]{1,24})!", r"\1,", text)       # "Hey Priya!" -> "Hey Priya,"
        text = text.replace("!", ".")
    if v.lowercase:
        text = text.lower()
    if v.emoji and not text.endswith(v.emoji):
        text = f"{text} {v.emoji}"
    return text


def _pick(options: list[str], seed: int) -> str:
    return options[seed % len(options)]


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
        hey = f"Hey {greeting_name(p.name)}" if greeting_name(p.name) else "Hey"   # never "Hey …0111"
        mine = [m for m in dm if m.is_from_me]
        v = voice_of(mine)
        candidates: list[Draft] = []

        def add(kind: str, why: str, options: list[str], value: float, detail: str = "",
                prestyled: bool = False) -> None:
            did = f"{kind}:{p.contact_id}"
            seed = seeds.get(did, 0)
            text = _pick(options, seed)
            candidates.append(Draft(did, kind, p.name, [p.contact_id], thread.id, why,
                                    text if prestyled else restyle(text, v), round(value, 1),
                                    detail, seed, len(options), v.__dict__))

        last = dm[-1]
        age = (now - last.ts).days
        if not last.is_from_me and "?" in last.text and 1 <= age <= 14:
            add("reply", f"{first} asked you something {age} day{'s' if age != 1 else ''} ago, "
                "and it's still the last message in your thread.",
                [f"{hey}! Just catching up on this.",
                 f"{hey}! Sorry, only just getting to this."],
                62 + 0.38 * p.importance, detail=last.text[:180])

        cut = now - timedelta(days=45)
        for m in reversed(dm):
            if m.ts < cut or m.is_from_me or (now - m.ts).days < 3:
                continue
            hit = EVENT.search(m.text)
            if not hit:
                continue
            event = hit.group(1).lower()
            # "How'd the interview go?" is a follow-up to "my job interview", so match
            # on the head noun, not the exact phrase they happened to use.
            key = event if event == "first day" else event.split()[-1].rstrip("s")
            followed = any(x.is_from_me and x.ts > m.ts and key in x.text.lower() for x in dm)
            if followed:
                break
            days = (now - m.ts).days
            add("follow_up", f"{first} mentioned their {event} {days} days ago, and you haven't "
                "asked about it since.",
                [f"{hey}! How did the {event} go?",
                 f"{hey}! Been thinking about your {event}. How'd it go?"],
                58 + 0.32 * p.importance, detail=m.text[:180])
            break

        stale = [pl for pl in plans if pl.state == "stale" and pl.thread_id == thread.id
                 and now - pl.last_activity <= timedelta(days=45)]
        if stale:
            did = f"revive_plan:{p.contact_id}"
            prop = propose(corpus, plans, [p.contact_id], thread.id, seeds.get(did, 0), now)
            candidates.append(Draft(did, "revive_plan", p.name, [p.contact_id], thread.id,
                                    prop.reasons[0] + " " + prop.reasons[1], restyle(prop.draft, v),
                                    round(50 + 0.35 * p.importance, 1), prop.when_label,
                                    prop.seed, prop.options, v.__dict__))

        if p.state == "quiet" and p.msgs_per_week > 1.2:
            add("reconnect", f"You used to talk about {p.msgs_per_week:.0f} times a week. "
                f"It's been {age} days.",
                [f"{hey}! You crossed my mind today. How've you been?",
                 f"{hey}! It's been way too long. What's new with you?",
                 f"{hey}! Saw something that reminded me of you. How are things?"],
                46 + 0.3 * p.warmth + min(15, age / 12))
        elif p.state in ("close", "steady"):
            opens = [c for i, c in enumerate(dm) if i == 0 or c.ts - dm[i - 1].ts > timedelta(hours=6)]
            gaps = [(b.ts - a.ts).days for a, b in zip(opens, opens[1:])]
            usual = median(gaps) if gaps else 7
            if age >= max(10, 2.5 * usual):
                add("check_in", f"One of your closer threads, and it's been {age} days "
                    f"when you'd usually talk every {max(1, round(usual))}.",
                    [f"{hey}! How's your week going?",
                     f"{hey}! Thinking of you. How are things?",
                     f"{hey}! What's new?"],
                    40 + 0.35 * p.importance)

        if candidates:
            out.append(max(candidates, key=lambda d: d.value))

    # Group plans: the circles where something was wanted recently and never dated.
    group_drafts = []
    importance = {p.contact_id: p.importance for p in profiles}
    for t in corpus.threads.values():
        if not t.is_group:
            continue
        recent = [pl for pl in plans if pl.thread_id == t.id and pl.state == "stale"
                  and now - pl.last_activity <= REVIVE_WINDOW]
        members = [c for c in t.participant_ids if c in corpus.contacts]
        if not recent or len(members) < 2:
            continue
        did = f"group_plan:{t.id}"
        prop = propose(corpus, plans, members, t.id, seeds.get(did, 0), now)
        v = voice_of([m for m in corpus.messages_in(t.id) if m.is_from_me])
        avg = sum(importance.get(c, 0) for c in members) / len(members)
        group_drafts.append(Draft(did, "group_plan", t.name, members, t.id,
                                  " ".join(prop.reasons[:2]), restyle(prop.draft, v),
                                  round(48 + 0.3 * avg, 1), prop.when_label, prop.seed,
                                  prop.options, v.__dict__))
    group_drafts.sort(key=lambda d: d.value, reverse=True)
    out += group_drafts[:2]

    out.sort(key=lambda d: d.value, reverse=True)
    return out[:limit]
