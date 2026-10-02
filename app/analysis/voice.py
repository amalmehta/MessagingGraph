"""Your texting voice, learned from what you've actually been sending lately.

Drafts are built from their parts -- who, what, when -- and then written the way you
text: greeting or not, lowercase or not, periods, question marks, apostrophes, "u"
and "wanna", "sat" or "saturday", "7" or "7pm", one message or a burst of short ones,
and how you usually ask to hang out.

It learns from your own sent messages in the last 10 days, widening to 30 or 90 if
there are too few to go on, because the way people text drifts. When you've texted a
particular person enough in that window, their thread refines it: most people write
differently to their best friend than to their aunt. Everything stays on this Mac.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from statistics import median

from ..models import Corpus, Message

WINDOWS = (10, 30, 90, None)
MIN_MESSAGES = 25          # enough of your own messages to say anything about your style
MIN_PERSON = 12            # enough with one person to prefer their thread's habits
GREETINGS = ("hey", "heyy", "heyyy", "hi", "hii", "hiya", "yo", "hello", "ayy", "ayo", "sup")
SHORTFORMS = [("u", r"\byou\b", r"\bu\b"), ("ur", r"\byour\b", r"\bur\b"),
              ("rn", r"\bright now\b", r"\brn\b"), ("tmrw", r"\btomorrow\b", r"\btmrw\b"),
              ("wanna", r"\bwant to\b", r"\bwanna\b"), ("gonna", r"\bgoing to\b", r"\bgonna\b"),
              ("lmk", r"\blet me know\b", r"\blmk\b"), ("pls", r"\bplease\b", r"\bpls\b")]
LONG = {"u": "you", "ur": "your", "rn": "right now", "tmrw": "tomorrow", "wanna": "want to",
        "gonna": "going to", "lmk": "let me know", "pls": "please"}
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
SHORT_DAYS = ["mon", "tues", "wed", "thurs", "fri", "sat", "sun"]
CONTRACTIONS = ["don't", "i'm", "what's", "how's", "that's", "can't", "you're", "it's", "i'll", "we're",
                "haven't", "how've", "how'd", "let's", "who's", "didn't", "isn't", "we've"]
LAUGHS = ["lol", "lmao", "hahaha", "haha"]
# Only these ever get added to a sincere message. A laughing face on "grateful for you"
# reads as sarcasm, however often you use it elsewhere.
WARM_EMOJI = ["❤️", "❤", "🥰", "😊", "🙂", "🫶", "💛", "💜", "🧡", "💕", "✨", "🙏", "🤗", "☺️"]
EMOJI = re.compile(r"[\U0001F300-\U0001FAFF☀-➿]")
QUESTIONY = re.compile(r"^(?:what|when|where|who|why|how|do|does|did|are|is|can|could|would|will|wanna|"
                       r"want|down|u |you |free|any|should)\b", re.I)
ASKS = {"wanna": r"\bwanna\b", "down": r"\bdown (?:to|for)\b", "free": r"\b(?:u|you) free\b",
        "lets": r"\blet'?s\b", "want": r"\bwant to\b", "should": r"\bwe should\b"}


@dataclass
class Style:
    window_days: int | None
    messages: int
    lowercase: bool = False
    greeting: str | None = "hey"
    periods: bool = False
    exclaim: bool = False
    question_marks: bool = True
    apostrophes: bool = True
    bursts: bool = False
    shortforms: set[str] = field(default_factory=set)
    short_days: bool = False
    times: str = "pm"                      # pm | bare | ish
    laugh: str | None = None
    emoji: str | None = None
    warm_emoji: str | None = None
    ask: str = "want"

    def describe(self) -> list[str]:
        """Plain-language traits, so you can see what your drafts are copying."""
        out = []
        out.append(f'starts with "{self.greeting}"' if self.greeting else "no greeting")
        out.append("lowercase" if self.lowercase else "capitalised")
        if not self.apostrophes:
            out.append("no apostrophes")
        if not self.question_marks:
            out.append("no question marks")
        if self.periods:
            out.append("ends with periods")
        if self.bursts:
            out.append("short bursts")
        out += [f'"{s}"' for s in sorted(self.shortforms)]
        out.append({"wanna": '"wanna…?"', "down": '"down to…?"', "free": '"you free…?"', "lets": '"let\'s…"',
                    "want": '"want to…?"', "should": '"we should…"'}[self.ask])
        if self.emoji:
            out.append(self.emoji)
        return out


def _share(items, pred) -> float:
    items = list(items)
    return sum(1 for x in items if pred(x)) / len(items) if items else 0.0


def _learn(mine: list[Message], openers: list[Message], window: int | None, double_share: float,
           base: Style | None = None) -> Style:
    s = Style(window, len(mine)) if base is None else Style(**{**base.__dict__, "shortforms": set(base.shortforms)})
    texts = [m.text.strip() for m in mine if m.text and m.text.strip()]
    if not texts:
        return s
    lows = [t for t in texts if t[0].isalpha()]
    s.lowercase = _share(lows, lambda t: t[0].islower()) > 0.6

    first_words = [re.sub(r"[^a-z]", "", t.split()[0].lower()) for t in (m.text.strip() for m in openers if m.text.strip())]
    greets = [w for w in first_words if w in GREETINGS]
    s.greeting = Counter(greets).most_common(1)[0][0] if first_words and len(greets) / len(first_words) >= 0.3 else None

    statements = [t for t in texts if len(t.split()) >= 3 and not QUESTIONY.match(t) and not t.endswith("?")]
    s.periods = _share(statements, lambda t: EMOJI.sub("", t).rstrip().endswith(".")) >= 0.4
    s.exclaim = _share(texts, lambda t: "!" in t) >= 0.25
    questions = [t for t in texts if QUESTIONY.match(t)]
    s.question_marks = not questions or _share(questions, lambda t: "?" in t) >= 0.5

    joined = " ".join(t.lower() for t in texts)
    with_ap = sum(joined.count(c) for c in CONTRACTIONS)
    without = sum(len(re.findall(r"\b" + c.replace("'", "") + r"\b", joined)) for c in CONTRACTIONS)
    s.apostrophes = with_ap >= without or with_ap + without < 3
    s.shortforms = {short for short, long_re, short_re in SHORTFORMS
                    if len(re.findall(short_re, joined)) >= 2 and len(re.findall(short_re, joined)) > len(re.findall(long_re, joined))}
    full_days = len(re.findall(r"\b(?:" + "|".join(DAYS) + r")\b", joined))
    short_days = len(re.findall(r"\b(?:mon|tues?|wed|thur?s?|fri|sat|sun)\b", joined))
    s.short_days = short_days > full_days and short_days >= 2
    pm = len(re.findall(r"\b\d{1,2}(?::\d\d)?\s?(?:am|pm)\b", joined))
    ish = len(re.findall(r"\b\d{1,2}ish\b", joined))
    bare = len(re.findall(r"\b(?:at|around|by)\s\d{1,2}\b(?!\s?(?:am|pm|:|ish))", joined))
    s.times = max((pm, "pm"), (bare, "bare"), (ish, "ish"))[1] if pm + bare + ish else "pm"
    # Bursts means several of your own messages back to back, not just short ones.
    s.bursts = double_share >= 0.25 or median(len(t.split()) for t in texts) <= 3
    laughs = Counter(l for t in texts for l in LAUGHS if re.search(r"\b" + l + r"\b", t.lower()))
    s.laugh = laughs.most_common(1)[0][0] if laughs and sum(laughs.values()) / len(texts) >= 0.08 else None
    emojis = Counter(ch for t in texts for ch in EMOJI.findall(t))
    s.emoji = emojis.most_common(1)[0][0] if emojis and _share(texts, lambda t: EMOJI.search(t)) >= 0.12 else None
    warm = Counter(e for t in texts for e in WARM_EMOJI if e in t)
    s.warm_emoji = warm.most_common(1)[0][0] if warm and sum(warm.values()) / len(texts) >= 0.06 else None
    asks = {k: len(re.findall(p, joined)) for k, p in ASKS.items()}
    if any(asks.values()):
        s.ask = max(asks, key=lambda k: asks[k])
    return s


_CACHE: dict = {}
_BASELINE: dict = {}


@dataclass
class _Baseline:
    """The half of your voice that doesn't depend on who you're writing to."""
    style: Style
    days: int | None
    mine: dict[str, list[Message]]         # your recent messages, per thread
    openers: dict[str, list[Message]]      # the ones that opened a conversation, per thread
    doubles: Counter


def _baseline(corpus: Corpus, now: datetime) -> _Baseline:
    # Every line here reads the whole corpus, and none of it depends on the contact, so it
    # runs once per corpus instead of once per person. A page of drafts asks for one style
    # per contact; doing this inside that loop made the first load quadratic.
    key = (id(corpus), len(corpus.messages), now)
    if key in _BASELINE:
        return _BASELINE[key]
    mine_all = [m for m in corpus.messages if m.is_from_me and m.text]
    days, recent = None, mine_all
    for days in WINDOWS:
        recent = mine_all if days is None else [m for m in mine_all if now - m.ts <= timedelta(days=days)]
        if len(recent) >= MIN_MESSAGES:
            break
    cutoff = None if days is None else now - timedelta(days=days)

    last: dict[str, Message] = {}
    openers: list[Message] = []
    doubles: Counter = Counter()
    for m in corpus.messages:                                  # chronological
        prev = last.get(m.thread_id)
        if m.is_from_me and m.text and (cutoff is None or m.ts >= cutoff):
            if prev is None or m.ts - prev.ts > timedelta(hours=6):
                openers.append(m)
            elif prev.is_from_me and m.ts - prev.ts <= timedelta(minutes=2):
                doubles[m.thread_id] += 1
        last[m.thread_id] = m

    style = _learn(recent, openers, days, sum(doubles.values()) / max(1, len(recent)))
    # Bucketed by thread so refining for one person costs that thread, not the whole history.
    by_thread: dict[str, list[Message]] = {}
    for m in recent:
        by_thread.setdefault(m.thread_id, []).append(m)
    opens_by_thread: dict[str, list[Message]] = {}
    for m in openers:
        opens_by_thread.setdefault(m.thread_id, []).append(m)

    base = _Baseline(style, days, by_thread, opens_by_thread, doubles)
    _BASELINE[key] = base
    return base


def style_for(corpus: Corpus, now: datetime, contact_id: str | None = None) -> Style:
    base = _baseline(corpus, now)
    if contact_id is None:
        return base.style
    key = (id(corpus), len(corpus.messages), now, contact_id)
    if key in _CACHE:
        return _CACHE[key]
    style = base.style
    thread = corpus.direct_thread(contact_id)
    if thread:
        mine = base.mine.get(thread.id, [])
        if len(mine) >= MIN_PERSON:
            person = _learn(mine, base.openers.get(thread.id, []), base.days,
                            base.doubles[thread.id] / len(mine), base=style)
            style = Style(**{**style.__dict__, **{a: getattr(person, a) for a in
                                                  ("greeting", "lowercase", "emoji", "warm_emoji", "exclaim", "laugh", "bursts")}})
    _CACHE[key] = style
    return style


# -- writing ------------------------------------------------------------------

VERBS = {"dinner": ("grab", "dinner"), "lunch": ("grab", "lunch"), "brunch": ("grab", "brunch"),
         "breakfast": ("grab", "breakfast"), "drinks": ("grab", "drinks"), "coffee": ("grab", "coffee"),
         "ramen": ("grab", "ramen"), "movie": ("see", "a movie"), "film": ("see", "a movie"),
         "show": ("go to", "the show"), "concert": ("go to", "the concert"), "gig": ("go to", "the gig"),
         "museum": ("hit", "the museum"), "farmers market": ("hit", "the farmers market"),
         "market": ("hit", "the market"), "beach": ("hit", "the beach"), "climbing gym": ("go", "climbing"),
         "climbing": ("go", "climbing"), "gym": ("hit", "the gym"), "karaoke": ("do", "karaoke"),
         "trivia": ("do", "trivia"), "hike": ("go on", "a hike"), "hiking": ("go", "hiking"),
         "picnic": ("do", "a picnic"), "bike ride": ("go on", "a bike ride"), "walk": ("go on", "a walk"),
         "run": ("go on", "a run"), "pottery class": ("do", "the pottery class"), "pottery": ("do", "pottery"),
         "board game night": ("do", "board game night"), "board games": ("play", "board games"),
         "game night": ("do", "game night"), "camping": ("go", "camping")}
PAST = {"grab": "grabbed", "see": "seen", "go to": "gone to", "hit": "hit", "go": "gone", "do": "done",
        "go on": "gone on", "play": "played"}
NEEDS_PLANNING = {"trip", "cabin", "vacation", "wedding", "birthday", "party", "visit"}


def day_words(when: datetime, now: datetime, style: Style) -> str:
    ahead = (when.date() - now.date()).days
    name = (SHORT_DAYS if style.short_days else DAYS)[when.weekday()]
    return name if ahead <= 6 else f"next {name}" if ahead <= 13 else f"{name} the {when.day}"


def time_words(hour: int, style: Style) -> str:
    if hour == 12:
        return "at noon"
    h = hour % 12 or 12
    return {"bare": f"at {h}", "ish": f"{h}ish", "pm": f"around {h}{'am' if hour < 12 else 'pm'}"}[style.times]


def _finish(line: str, kind: str, style: Style) -> str:
    """kind: greet | say | ask"""
    for short in sorted(style.shortforms):
        # Not before an apostrophe: "you're" is a contraction, not a spelled-out "u".
        line = re.sub(r"\b" + LONG[short] + r"\b(?!['\u2019])", short, line)
    if not style.apostrophes:
        line = re.sub(r"\bhow've (u|you) been\b", r"how \1 been", line)    # nobody types "howve"
        for c in CONTRACTIONS:
            line = re.sub(r"\b" + re.escape(c) + r"\b", c.replace("'", ""), line)
    line = line.rstrip()
    if kind == "ask":
        line += "?" if style.question_marks else ""
    elif kind == "greet":
        line += "!" if style.exclaim else ""
    elif style.periods:
        line += "."
    return line


def write(parts: list[tuple[str, str]], style: Style, warm: bool = False) -> str:
    """parts: (kind, text) in order. Returns the draft as you'd send it."""
    lines = [_finish(text, kind, style) for kind, text in parts if text]
    if warm and style.warm_emoji:
        lines[-1] = f"{lines[-1]} {style.warm_emoji}"
    if style.bursts:
        out = "\n".join(lines)
    else:
        out, prev_kind = "", None
        for (kind, _), line in zip([p for p in parts if p[1]], lines):
            if not out:
                out = line
            elif prev_kind == "greet" and not out.endswith(("!", "?", ".")):
                out += ", " + line
            elif out.endswith((".", "!", "?")):
                out += " " + line
            else:
                out += "\n" + line          # people who don't punctuate send separate messages
            prev_kind = kind
    return _case(out, style)


MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september",
          "october", "november", "december"]
_PROPER = re.compile(r"\b(" + "|".join(DAYS + SHORT_DAYS + MONTHS) + r")\b")


def _case(text: str, style: Style) -> str:
    if style.lowercase:
        return text.lower()
    text = _PROPER.sub(lambda m: m.group(1).capitalize(), text)
    # sentence case: capital after line starts and sentence ends, never after a comma
    chars, cap = list(text), True
    for i, ch in enumerate(chars):
        if cap and ch.isalpha():
            chars[i], cap = ch.upper(), False
        elif ch in ".!?\n":
            cap = True
    return re.sub(r"\bi\b", "I", "".join(chars))


def greet(style: Style, name: str | None, group: bool = False) -> tuple[str, str]:
    if not style.greeting:
        return ("greet", "")
    if group:
        return ("greet", f"{style.greeting} {name or 'all'}")          # "hey you two" / "hey all"
    return ("greet", f"{style.greeting} {name}" if name else style.greeting)


IT = {"grab": "go", "see": "go", "go to": "go", "hit": "go", "go": "go", "do": "do it", "go on": "go",
      "play": "play"}


def plan_parts(style: Style, activity: str, when: datetime, now: datetime, hour: int,
               name: str | None, group: bool, revive: bool, seed: int) -> list[tuple[str, str]]:
    activity = activity.lower()
    day = day_words(when, now, style)
    parts = [greet(style, name, group)]
    in_q = "who's in" if group else "you in"
    if activity in NEEDS_PLANNING:
        noun = f"the {activity}" if activity != "visit" else "a visit"
        want = "wanna" if style.ask == "wanna" else "want to"
        # Three, like the branch below: callers advertise three phrasings per activity,
        # and two here meant "another version" handed back a draft you'd already seen.
        options = [[("ask", f"{want} finally plan {noun}"), ("ask", f"you free to figure it out {day}")],
                   [("say", f"we still haven't planned {noun}"), ("ask", f"can we sort it out {day}")],
                   [("say", f"been meaning to get {noun} on the calendar"),
                    ("ask", f"{day} work for figuring it out")]]
        return parts + options[seed % len(options)]
    verb, obj = VERBS.get(activity, ("do", activity))
    at = time_words(hour, style)
    doing = f"{verb} {obj}"
    it = IT.get(verb, "do it")

    def asking(what: str) -> list[tuple[str, str]]:
        return {"wanna": [("ask", f"wanna {what} {day} {at}")],
                "down": [("ask", f"down to {what} {day} {at}")],
                "free": [("ask", f"you free {day} {at}")],
                "lets": [("say", f"let's {what} {day} {at}"), ("ask", in_q)],
                "want": [("ask", f"want to {what} {day} {at}")],
                "should": [("say", f"we should {what} {day} {at}"), ("ask", in_q)]}[style.ask]

    recall = f"we still haven't {PAST.get(verb, verb)} {obj}" if revive else "it's been a minute"
    # Reviving, the opener names the activity, so the ask can just say "go". Otherwise the
    # ask has to carry it: "it's been a minute / wanna go sat at 7" never says to do what,
    # and every activity sharing an hour comes out as the same message.
    options = [
        [("say", recall)] + asking(it if revive else doing),          # "we still haven't grabbed dinner / wanna go sat at 7"
        asking(doing) + ([("ask", in_q)] if group and style.ask not in ("lets", "should") else []),
        [("say", f"been wanting to {doing}"), ("ask", f"{day} {at} work")],
    ]
    if style.ask == "free":
        options[0] = [("say", recall), ("ask", f"you free {day} {at}" if revive
                                       else f"you free to {doing} {day} {at}")]
        options[1] = [("ask", f"you free {day} {at}"), ("say", f"could {doing}")]
    return parts + options[seed % len(options)]


def reconnect_parts(style: Style, name: str | None, seed: int) -> list[tuple[str, str]]:
    options = [[("say", "it's been way too long"), ("ask", "how've you been")],
               [("say", "you crossed my mind today"), ("ask", "how are things")],
               [("ask", "how've you been"), ("say", "feels like forever")]]
    return [greet(style, name)] + options[seed % len(options)]


def check_in_parts(style: Style, name: str | None, seed: int) -> list[tuple[str, str]]:
    options = [[("ask", "how's your week going")], [("ask", "what's new")], [("ask", "how are things")]]
    return [greet(style, name)] + options[seed % len(options)]


def follow_up_parts(style: Style, name: str | None, event: str, seed: int) -> list[tuple[str, str]]:
    options = [[("ask", f"how did the {event} go")],
               [("say", f"been thinking about your {event}"), ("ask", "how'd it go")]]
    return [greet(style, name)] + options[seed % len(options)]


def reply_parts(style: Style, name: str | None, seed: int) -> list[tuple[str, str]]:
    options = [[("say", "sorry just seeing this")], [("say", "just catching up on this")]]
    return [greet(style, name)] + options[seed % len(options)]


def thanks_parts(style: Style, name: str | None, kind: str, seed: int, what: str = "", when: str = "",
                 span: str = "", phrase: str = "") -> list[tuple[str, str]]:
    if kind == "thanked":
        gerund = what.split()[0].lower().endswith("ing") if what else False
        options = [[("say", f"still really grateful for you {what} {when}" if gerund
                     else f"still really grateful for {what} {when}")],
                   [("say", f"randomly thought about {'you ' if gerund else ''}{what} {when}"), ("say", "still so grateful")]]
    elif kind == "anniversary":
        options = [[("say", f"just realized it's been {span} since we started texting"), ("say", "glad it's you")],
                   [("say", f"{span} of texting this week"), ("say", "grateful for you")]]
    elif kind == "together":
        options = [[("say", f"still smiling about {phrase} {when}"), ("say", "grateful for you")],
                   [("say", f"thanks again for {phrase} {when}"), ("say", "always good times with you")],
                   [("say", f"kept thinking about {phrase} {when}"), ("say", "thanks for always being up for things")]]
    else:
        options = [[("say", "no reason, just grateful you're in my life")], [("say", "random but I really appreciate you")]]
    return [greet(style, name)] + options[seed % len(options)]
