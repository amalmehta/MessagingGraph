"""Vibes: the tone of a relationship, and a short description with some personality.

**Mood** is sentiment read the way people text. Off-the-shelf VADER scores "miss
you" as negative and "I'm dead 😭😭" as grim, which would call your silliest
friendships bleak. So each score passes through a texting layer: laughter lifts a
message, affection reads warm, politeness isn't sadness, short banter is neutral --
and clearly sad, hurt or grieving language always reads as heavy.

**Vibe** is eight dimensions turned into a name and one line of imagery. Which
dimensions define someone is decided *relative to your other relationships*, the
same way self-editing is measured against your own baseline -- otherwise everyone
you like comes out "playful and tender" and every description reads the same.

Neither feeds connection state or balance. A heavy stretch usually means someone
trusted the other with something hard. The imagery avoids famous or romantic
pairings, because it gets read about siblings and coworkers too.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta
from functools import lru_cache
from statistics import mean, pstdev

from ..models import Corpus, Message, Plan, RelationshipProfile, Vibe
from . import lexicon

# Every pattern here is lowercase, and the text is folded before it is matched.
# Asking the engine to ignore case instead cost four times as much -- _PRACTICAL
# alone was 437ms of every recompute, against 101ms for the same pattern run
# against folded text.
_LAUGH = re.compile(r"\b(?:ha(?:ha)+h?|he(?:he)+|lol+|lmf?ao+|rofl|i'?m dead|screaming|"
                    r"i'?m crying|im crying)\b|😂|🤣|💀")
_HURT = re.compile(
    r"\b(?:sad|hurt|hurting|angry|upset|hate|awful|terrible|depress\w*|lonely|scared|"
    r"worried|anxious|funeral|passed away|died|grief|grieving|struggl\w*|exhausted|"
    r"overwhelmed|miserable|heartbroken|cancer|hospital|breakup|broke up|laid off)\b")
# "that was sick", "this is fire": slang praise VADER reads as illness or danger.
_SLANG_UP = re.compile(r"\b(?:that|this)(?:\s+\w+)?(?:'s|\s+is|\s+was|\s+looks|\s+looked|\s+sounds|"
                       r"\s+sounded)\s+(?:so |lowkey |actually )?(?:sick|fire|insane|nuts|unreal|wild)\b"
                       r"|\bit'?s (?:so )?(?:sick|fire)\b")
_BANTER = re.compile(r"^(?:stop+|no way|absolutely not|the audacity|unhinged|i can'?t+|"
                     r"shut up|you'?re kidding|no+|omg+|bruh+|dead)\b")

_ANALYZER = None
_CACHE: dict[str, float] = {}


def mood_of(text: str) -> float:
    """-1..1 for one message, corrected for how people text."""
    global _ANALYZER
    if not text:
        return 0.0
    if _ANALYZER is None:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
        _ANALYZER = SentimentIntensityAnalyzer()
    c = _ANALYZER.polarity_scores(text)["compound"]
    low = text.casefold()
    if _HURT.search(low):
        return max(-1.0, min(c, -0.35))            # grief with a "lol" is still grief
    if _SLANG_UP.search(low) and c < 0.45:
        c = 0.45
    if _LAUGH.search(low):
        c = 0.35 + 0.5 * max(c, 0.0)
    if lexicon.affection_score(text) >= 1 and c < 0.45:
        c = 0.45
    if c < 0:
        if lexicon.hedge_score(text) >= 1 and c > -0.5:
            c = 0.0                                 # politeness, not sadness
        elif len(text.split()) <= 5 and _BANTER.search(low.strip()):
            c = 0.0                                 # banter, not rejection
    return max(-1.0, min(1.0, c))


def _mood(m: Message) -> float:
    v = _CACHE.get(m.id)
    if v is None:
        v = _CACHE[m.id] = mood_of(m.text)
    return v


_PRACTICAL = re.compile(
    r"\b(?:grab|pick(?:ing)? up|drop(?:ping)? off|running late|on my way|omw|eta|package|"
    r"delivery|rent|bill|paid|venmo|landlord|wifi|trash|groceries|milk|keys?|parking|"
    r"what time|meeting|standup|deck|doc|review|sprint|deadline|client|invoice|schedule|"
    r"reschedul\w*|calendar|feed the cat|back around)\b")
_EXCLAIM = re.compile(r"!|[\U0001F300-\U0001FAFF☀-➿]")
DIMENSIONS = ("playful", "tender", "lively", "deep", "steady", "practical", "planning", "night_owl")


# Laughing, exclaiming and doing logistics: three things a message either is or
# isn't, and the answer depends on nothing but the text. Kept for the same reason
# the lexicon scores are: excluding one person recomputes everything, and the
# messages it reads the second time are the same ones.
@lru_cache(maxsize=None)
def _traits(text: str) -> tuple[bool, bool, bool]:
    low = text.casefold()
    return bool(_LAUGH.search(low)), bool(_EXCLAIM.search(low)), bool(_PRACTICAL.search(low))


def _cap(x: float) -> float:
    return round(100 * max(0.0, min(1.0, x)), 1)


def _dimensions(dm: list[Message], p: RelationshipProfile, plans: list[Plan],
                now: datetime) -> dict[str, float]:
    texted = [m for m in dm if m.text]
    n = max(1, len(texted))
    laughs = warms = cares = exclaims = practicals = 0
    for m in texted:
        laughed, exclaimed, logistics = _traits(m.text)
        laughs += laughed
        warms += lexicon.affection_score(m.text) >= 1
        cares += lexicon.is_care_question(m.text)
        exclaims += exclaimed
        practicals += logistics
    laugh = laughs / n
    playful = laugh / 0.35 + 3 * sum(m.reactions.count("laugh") for m in dm) / max(1, len(dm))
    warm = warms / n
    care = cares / n
    tender = warm / 0.4 + 0.3 * care / 0.15 + 3 * sum(m.reactions.count("love") for m in dm) / max(1, len(dm))

    convos: list[list[Message]] = []
    for m in dm:
        if not convos or m.ts - convos[-1][-1].ts > timedelta(hours=6):
            convos.append([])
        convos[-1].append(m)
    burst = mean(len(c) for c in convos) if convos else 0.0
    exclaim = exclaims / n
    # Only the sides that exist. With neither, "lively" leans on the two signals
    # that are real rather than scoring a speed nobody has.
    speeds = [x for x in (p.my_median_reply_s, p.their_median_reply_s) if x is not None]
    if speeds:
        reply = sum(speeds) / len(speeds)
        quick = 1.0 if reply < 600 else 0.0 if reply > 10800 else 1 - (reply - 600) / 10200
        lively = 0.4 * burst / 10 + 0.3 * exclaim / 0.35 + 0.3 * quick
    else:
        lively = (0.4 * burst / 10 + 0.3 * exclaim / 0.35) / 0.7

    practical = practicals / n / 0.35
    opens = [c[0] for c in convos]
    night = sum(1 for m in opens if m.ts.hour >= 22 or m.ts.hour < 4) / max(1, len(opens))

    steady = 0.0
    if dm:
        weeks = max(1, int((now - max(dm[0].ts, now - timedelta(weeks=26))).days // 7))
        if weeks >= 6:
            counts = [0] * weeks
            for m in dm:
                i = int((now - m.ts).days // 7)
                if 0 <= i < weeks:
                    counts[i] += 1
            avg = mean(counts)
            steady = 0.0 if avg == 0 else 1 - (pstdev(counts) / avg) / 1.5
    span = max(4.0, (now - dm[0].ts).days / 7) if dm else 4.0
    planning = len(plans) / span / 1.2

    # Uncapped, for ranking: a 0-100 cap turns "very tender" and "extremely tender"
    # into a tie, and tender stops ever defining anyone. The card shows capped values.
    return {"playful": playful, "tender": tender, "lively": lively, "deep": p.depth / 100,
            "steady": steady, "practical": practical, "planning": planning, "night_owl": night / 0.3}


PAIRS: dict[tuple[str, str], list[tuple[str, str]]] = {
    ("playful", "tender"): [("Warm chaos", "Can't get through a sentence without laughing, and means every 'love you'."),
                           ("Sweet and a little unhinged", "Heart emojis and terrible jokes in roughly equal measure.")],
    ("playful", "lively"): [("Full volume", "A whole group chat's worth of energy squeezed into two people."),
                           ("Rapid-fire comedy", "The reply lands before the other person has put their phone down.")],
    ("playful", "deep"): [("Silly, then suddenly real", "Jokes for an hour, then the conversation you actually needed.")],
    ("playful", "night_owl"): [("Late-night comedy hour", "Nothing is funnier than it is after midnight.")],
    ("playful", "planning"): [("Partners in mischief", "Every other message is a scheme for the weekend.")],
    ("playful", "practical"): [("Errands with a laugh track", "Even 'did you grab milk' comes with a bit.")],
    ("playful", "steady"): [("The long-running bit", "An inside joke that's been going for months and isn't finished.")],
    ("tender", "deep"): [("Soft place to land", "Where the real stuff gets said, and gets held.")],
    ("tender", "steady"): [("Sunday-call warmth", "Checking in because that's simply what you two do."),
                          ("Old reliable", "The kind of steady that's easy to take for granted, and worth not.")],
    ("tender", "playful"): [("Sweet and goofy", "Kindness first, puns a close second.")],
    ("tender", "lively"): [("Big-hearted and chatty", "A lot of words, and most of them kind.")],
    ("tender", "night_owl"): [("Midnight heart-to-hearts", "Starts with 'you up?' and ends somewhere honest.")],
    ("tender", "planning"): [("Always making time", "The one who actually puts the hang on the calendar.")],
    ("deep", "night_owl"): [("3 a.m. philosophers", "Long messages, late hours, the big questions.")],
    ("deep", "tender"): [("Kindred spirits", "Few words wasted, and the ones that get sent carry weight.")],
    ("deep", "steady"): [("Slow and meaningful", "Not every day, but every time it counts.")],
    ("deep", "playful"): [("Letters with jokes in the margins", "Long, thoughtful, and never too serious for long.")],
    ("lively", "practical"): [("Well-oiled machine", "Fast, frequent, and everything gets handled.")],
    ("lively", "playful"): [("Always on", "Never more than a notification away from the next bit.")],
    ("lively", "planning"): [("Instigators", "The thread where 'we should' turns into 'I'm outside'.")],
    ("steady", "practical"): [("Dependable co-pilots", "Nothing flashy, just two people who reliably show up.")],
    ("steady", "tender"): [("Background warmth", "A consistent hum of care running under your weeks.")],
    ("practical", "lively"): [("Tiny, efficient company", "Co-founders of the business of everyday life.")],
    ("practical", "steady"): [("Logistics, lovingly", "Mostly who's picking up what, which is its own kind of trust.")],
    ("practical", "deep"): [("Work friends, real friends", "Starts with the deadline, ends up somewhere more human.")],
    ("planning", "playful"): [("Adventure schemers", "Always cooking up the next outing; half the fun is the planning.")],
    ("planning", "practical"): [("The planning committee", "Calendars out, dates set, things on the books.")],
    ("planning", "lively"): [("Big on 'we should'", "Full of ideas for things to do together, and the energy to match.")],
    ("night_owl", "deep"): [("Night owls", "Your best conversations happen after everyone else is asleep.")],
}
SINGLES: dict[str, tuple[str, str]] = {
    "playful": ("Easy laughter", "Most of what passes between you is a good time."),
    "tender": ("Quietly devoted", "Not loud about it, but the care shows up every time."),
    "deep": ("Deep water", "Fewer messages, more meaning per message."),
    "lively": ("Bright and busy", "A quick, cheerful back-and-forth that rarely pauses."),
    "steady": ("Slow and sure", "A constant, easy presence in the background."),
    "practical": ("Getting things done", "Plans, pickups and 'running late', handled without fuss."),
    "planning": ("The social director", "Full of ideas for things to do together."),
    "night_owl": ("After hours", "The thread that wakes up when everyone else goes to sleep."),
}
STATES = {
    "new": [("Just getting started", "Too early for a vibe; these are the first few pages."),
            ("First few pages", "Not much written yet, which is how every good thread starts.")],
    "quiet_warm": [("Paused, not over", "Warm history, quiet present, the kind that picks up where it left off."),
                   ("Bookmarked", "Nothing's wrong, just a long pause between chapters.")],
    "quiet": [("On a quiet stretch", "Life got busy. The thread's still there whenever either of you is."),
              ("Between chapters", "Busy lives and a quiet thread, with no reason it can't pick back up.")],
    "group": [("In the group chat", "You mostly share a group chat rather than a one-on-one thread.")],
    "uneven": [("Porch light on", "You've been keeping this one lit lately, which says something good about you.")],
    "uneven_careful": [("Careful and considerate", "You choose your words thoughtfully here, which can simply mean you care how they land.")],
}


def _first_unused(options: list[tuple[str, str]], used: set[str]) -> tuple[str, str]:
    return next((o for o in options if o[0] not in used), options[0])


def _mood_shift(texted: list[Message], now: datetime) -> tuple[str, str]:
    cut = now - timedelta(weeks=8)
    recent = [_mood(m) for m in texted if m.ts >= cut]
    earlier = [_mood(m) for m in texted if m.ts < cut]
    if len(recent) < 15 or len(earlier) < 15:
        return ("not enough to say", "Not enough messages either side of the last two months to read a change in tone.")
    delta = 100 * (mean(recent) - mean(earlier))
    if delta >= 12:
        return ("brighter lately", "Lighter than it used to be. Could be good news on either side, or just a sunnier stretch.")
    if delta <= -12:
        return ("heavier lately", "A heavier stretch. That's often what it looks like when someone trusts the other "
                "with something hard, not a sign anything is wrong between you.")
    return ("steady", "About the same tone it's always had.")


def attach_vibes(corpus: Corpus, profiles: list[RelationshipProfile],
                 plans: list[Plan], now: datetime) -> None:
    rows = []
    for p in profiles:
        thread = corpus.direct_thread(p.contact_id)
        dm = corpus.messages_in(thread.id) if thread else []
        mine_plans = [pl for pl in plans if p.contact_id in pl.participant_ids]
        rows.append((p, dm, _dimensions(dm, p, mine_plans, now)))

    # Percentile of each dimension among relationships with enough history, so
    # "top" means "more than your other relationships", not "high in absolute terms".
    peers = [d for p, _, d in rows if p.state not in ("new",)] or [d for _, _, d in rows]
    def pct(k: str, v: float) -> float:
        vals = [d[k] for d in peers]
        return sum(1 for x in vals if x < v) + 0.5 * sum(1 for x in vals if x == v)

    used: set[str] = set()
    for p, dm, raw in rows:
        dims = {k: _cap(v) for k, v in raw.items()}
        ranked = sorted(DIMENSIONS, key=lambda k: (pct(k, raw[k]), raw[k]), reverse=True)
        top = ranked[:2]
        if p.state == "group":
            name, line = STATES["group"][0]           # shared by design: it's a category, not a portrait
        elif p.state == "new":
            name, line = _first_unused(STATES["new"], used)
        elif p.state == "quiet":
            name, line = _first_unused(
                STATES["quiet_warm" if max(dims["tender"], dims["playful"]) >= 45 else "quiet"], used)
        elif p.state == "uneven":
            name, line = _first_unused(
                STATES["uneven_careful" if p.self_editing >= 70 else "uneven"], used)
        else:
            options = [o for a in ranked[:3] for b in ranked[:4] if a != b
                       for o in PAIRS.get((a, b), [])] + [SINGLES[ranked[0]]]
            start = int(hashlib.sha1(p.contact_id.encode()).hexdigest(), 16) % max(1, len(options) - 1)
            fresh = [o for o in options if o[0] not in used]
            name, line = fresh[0] if fresh else options[start]
            for a in ranked[:3]:
                for b in ranked[:4]:
                    if (name, line) in PAIRS.get((a, b), []):
                        top = [a, b]
        used.add(name)

        texted = [m for m in dm if m.text]
        mine = [_mood(m) for m in texted if m.is_from_me]
        theirs = [_mood(m) for m in texted if not m.is_from_me]
        every = mine + theirs
        shift, note = _mood_shift(texted, now)
        p.vibe = Vibe(
            name=name, line=line,
            # statistics.mean, not sum()/len: it is slower, and it is also the
            # difference between a mood of 71.4 and 71.3 for one person here.
            # A displayed number is not worth a fifth of a second.
            mood=round(100 * mean(every), 1) if every else 0.0,
            mood_theirs=round(100 * mean(theirs), 1) if theirs else 0.0,
            mood_mine=round(100 * mean(mine), 1) if mine else 0.0,
            positive_share=round(sum(1 for v in every if v >= 0.25) / max(1, len(every)), 3),
            heavy_share=round(sum(1 for v in every if v <= -0.25) / max(1, len(every)), 3),
            mood_shift=shift, mood_note=note, dimensions=dims, top=top,
        )
