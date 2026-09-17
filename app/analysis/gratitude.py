"""Gratitude: thank-you messages grounded in something that actually happened.

Where a thank-you comes from, in order of how much it would likely mean:

1. **Something you thanked them for, a while ago.** "thanks so much for helping me
   move" resurfaced a month or more later -- "still grateful to you for helping me
   move" -- lands because it shows it stayed with you.
2. **A texting anniversary.** Only when the first message is genuinely the start:
   an import covers a fixed window, so a first message near the start of your
   whole history probably just marks where the import began.
3. **Something you did together**, from plans that actually happened.
4. **Plain appreciation**, for a close, long-running thread with nothing more
   specific to point to.

Small logistics thanks ("thanks for the info") aren't gratitude moments, and
anything about health, grief or money is left for you to write yourself. Nothing
here sends anything.
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timedelta

from ..models import Corpus, Plan, RelationshipProfile
from ..names import greeting_name, short_name
from .circles import activity_of
from .messages import Draft, restyle, voice_of

THANKS = re.compile(r"\b(?:thank you|thanks|thank u|thx|ty)(?:\s+(?:so much|again|a ton|a lot|so so much))?,?"
                    r"\s+for\s+(?P<what>[^.!?\n]{3,90})", re.I)
TRIVIAL = re.compile(r"^(?:the\s+(?:info|update|reminder|heads\s?up|link|tip|rec|recommendation|invite|offer)|"
                     r"letting me know|checking(?: in)?|asking|reaching out|that|this|it|everything|nothing|"
                     r"coming|your (?:help|time)$)\b", re.I)
SENSITIVE = re.compile(r"\b(?:hospital|surgery|funeral|passed away|died|grief|therapy|therapist|diagnos\w*|"
                       r"cancer|chemo|rehab|money|loan|lend\w*|rent|pay(?:ing)?|paid|venmo|cash|bail|debt)\b", re.I)
CUT = re.compile(r"\s+(?:and i|but|lol|haha|omg|seriously|honestly|i really|you're the best|"
                 r"today|tonight|yesterday|last night|earlier|this morning)\b.*$", re.I)
ELIGIBLE = {"close", "steady", "practical", "uneven", "quiet"}
# How a shared activity reads when you're looking back on it.
LOOKING_BACK = {"coffee": "that coffee", "brunch": "that brunch", "dinner": "that dinner", "drinks": "those drinks",
                "lunch": "that lunch", "breakfast": "that breakfast", "karaoke": "karaoke night",
                "trivia": "trivia night", "ramen": "that ramen", "show": "that show", "concert": "that concert",
                "gig": "that gig", "climbing gym": "climbing", "climbing": "climbing", "hike": "that hike",
                "hiking": "that hike", "picnic": "that picnic", "movie": "that movie", "film": "that film",
                "farmers market": "the farmers market", "market": "the market", "museum": "the museum",
                "bike ride": "that bike ride", "board game night": "board game night", "game night": "game night",
                "board games": "board games", "pottery class": "the pottery class", "pottery": "pottery",
                "trip": "that trip", "cabin": "the cabin trip", "beach": "the beach day", "camping": "camping"}
YOURS = {"me": "you", "my": "your", "mine": "yours", "myself": "yourself", "i": "you", "i'm": "you're"}
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
          "October", "November", "December"]


def _clean_what(raw: str) -> str | None:
    what = re.sub(r"[\U0001F300-\U0001FAFF☀-➿]+", "", raw).strip(" ,;:-")
    what = CUT.sub("", what).strip(" ,;:-")
    words = what.split()
    if len(words) < 2 or len(words) > 9 or TRIVIAL.match(what) or SENSITIVE.search(what):
        return None
    return what


def _when(ts: datetime, now: datetime) -> str:
    days = (now - ts).days
    if days < 45:
        return "a few weeks ago"
    return f"back in {MONTHS[ts.month - 1]}" if days < 330 else f"last {MONTHS[ts.month - 1]}"


def build_gratitude(corpus: Corpus, profiles: list[RelationshipProfile], plans: list[Plan],
                    now: datetime, seeds: dict[str, int] | None = None, limit: int = 6) -> list[Draft]:
    seeds = seeds or {}
    history_start = corpus.messages[0].ts if corpus.messages else now
    out: list[Draft] = []
    used: Counter = Counter()                  # (kind, wording) already shown higher in the list
    chosen: dict[str, tuple[str, int]] = {}
    for p in profiles:
        if p.state not in ELIGIBLE:
            continue
        thread = corpus.direct_thread(p.contact_id)
        dm = corpus.messages_in(thread.id) if thread else []
        if not dm:
            continue
        v = voice_of([m for m in dm if m.is_from_me])
        g = greeting_name(p.name)
        hey = f"Hey {g}" if g else "Hey"
        first = short_name(p.name)
        options: list[Draft] = []

        # Each person starts on the wording used least so far, so a list of thank-yous
        # doesn't read as one template. "Another version" steps on from there.
        offset = sum(map(ord, p.contact_id))

        def add(kind: str, why: str, texts: list[str], value: float, detail: str = "") -> None:
            did = f"thanks:{kind}:{p.contact_id}"
            start = min(range(len(texts)), key=lambda i: (used[(kind, i)], (i - offset) % len(texts)))
            idx = (start + seeds.get(did, 0)) % len(texts)
            chosen[did] = (kind, idx)
            options.append(Draft(did, kind, p.name, [p.contact_id], thread.id, why,
                                 restyle(texts[idx], v), round(value, 1), detail,
                                 seeds.get(did, 0), len(texts), v.__dict__))

        # 1. Something you thanked them for, at least three weeks ago.
        for m in reversed(dm):
            age = (now - m.ts).days
            if not m.is_from_me or age < 21 or age > 400:
                continue
            hit = THANKS.search(m.text)
            what = _clean_what(hit.group("what")) if hit else None
            if not what:
                continue
            when = _when(m.ts, now)
            gerund = what.split()[0].lower().endswith("ing")
            yours = " ".join(YOURS.get(w.lower(), w) for w in what.split())   # "helping me move" -> "helping you move"
            add("thanked", f"You thanked {first} for {yours} {when}.",
                [f"{hey}! Still really grateful to you for {what} {when}.",
                 (f"{hey}! Randomly thought about you {what} {when}. Still grateful." if gerund else
                  f"{hey}! Randomly thought about {what} {when}. Still grateful for that.")],
                72 + 0.3 * p.importance, detail=m.text[:140])
            break

        # 2. A real texting anniversary, within a week either side.
        started = dm[0].ts
        years = int((now - started).days // 365.25)
        if years >= 1 and started - history_start > timedelta(days=30):
            nearest = started.replace(year=started.year + years)
            if abs((now - nearest).days) <= 7:
                span = "a year" if years == 1 else f"{years} years"
                add("anniversary", f"You and {first} started texting {span} ago this week.",
                    [f"{hey}! Just realised it's been {span} since we started texting. Glad it's you.",
                     f"{hey}! {span.capitalize()} of texting this week. Grateful for you."],
                    66 + 0.2 * p.importance)

        # 3. Something you actually did together, a couple of weeks back or more.
        done = sorted((pl for pl in plans if pl.thread_id == thread.id and pl.state == "done"
                       and 14 <= (now - pl.last_activity).days <= 180),
                      key=lambda pl: pl.last_activity, reverse=True)
        if done:
            act = activity_of(done[0]).lower()
            phrase = LOOKING_BACK.get(act, f"that {act}")
            when = _when(done[0].last_activity, now)
            add("together", f"Something you did with {first} {when}: {act}.",
                [f"{hey}! Still smiling about {phrase} {when}. Grateful for you.",
                 f"{hey}! Thanks again for {phrase} {when}. Always good times with you.",
                 f"{hey}! Kept thinking about {phrase} {when}. Thanks for always being up for things."],
                56 + 0.3 * p.importance)

        # 4. Plain appreciation for a close thread that's lasted.
        if p.state in ("close", "steady") and (now - started).days >= 150:
            add("appreciation", f"{first} has been a steady, warm presence in your messages for months.",
                [f"{hey}! No reason, just grateful you're in my life.",
                 f"{hey}! Random, but I really appreciate you."],
                36 + 0.25 * p.importance)

        if options:
            best = max(options, key=lambda d: d.value)
            used[chosen[best.id]] += 1
            out.append(best)
    out.sort(key=lambda d: d.value, reverse=True)
    return out[:limit]
