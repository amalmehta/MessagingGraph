"""Plan extraction and the plan state machine.

Plans are read straight out of message text -- no labels, no hidden metadata --
so this works identically on a real message store. A plan is seeded when someone
names an activity with intent, then advances through:

    floated -> proposed -> confirmed -> scheduled -> done
                   \\-> stale        \\-> dropped

`stale` is the state the whole feature exists for: the plan everyone agreed to
and nobody ever put a date on.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from ..models import Corpus, Message, Plan, SELF_ID

CONVO_GAP = timedelta(hours=6)
MERGE_WINDOW = timedelta(days=21)
STALE_AFTER = timedelta(days=18)

ACTIVITIES = [
    "dinner", "drinks", "coffee", "brunch", "lunch", "breakfast", "hike", "hiking",
    "climbing gym", "climbing", "gym", "movie", "film", "farmers market", "market",
    "trivia", "pottery class", "pottery", "ramen", "picnic", "museum", "karaoke",
    "show", "concert", "gig", "bike ride", "board game night", "board games",
    "game night", "trip", "cabin", "party", "birthday", "wedding", "catch up",
    "hang out", "hang", "visit", "call", "walk", "run", "beach", "camping",
]
_ACT = re.compile(r"\b(" + "|".join(sorted(ACTIVITIES, key=len, reverse=True)) + r")\b", re.I)

INTENT = re.compile(
    r"\b(?:we should|should we|let'?s|want to|wanna|when are we|are you (?:free|around|down)|"
    r"how about|down for|overdue for|thinking (?:about|of)|you free|i want to|"
    r"any interest|up for|do you want to|we need to)\b", re.I)

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_DAY = re.compile(
    r"\b(?:(next|this)\s+)?(" + "|".join(WEEKDAYS) + r"|weekend|week|tonight|tomorrow|"
    r"\d{1,2}(?:st|nd|rd|th))\b", re.I)
_TIME = re.compile(r"\b(?:\d{1,2}(?::\d{2})?\s*(?:am|pm)|\d{1,2}:\d{2}|noon|midnight)\b", re.I)

_YES = re.compile(
    r"\b(?:i'?m in|yes|yep|yeah|down|count me in|book it|let'?s do it|that works|"
    r"perfect|sounds (?:great|good)|works for me|i'?m down|deal|sure)\b", re.I)
_NO = re.compile(
    r"\b(?:can'?t|cannot|rain ?check|push it|bail|out of town|something came up|"
    r"next time|another time|won'?t make it|have to skip)\b", re.I)
_MAYBE = re.compile(r"\b(?:might|tentativ|i'?ll try|let me (?:check|see)|probably\?|i'?ll confirm)\b", re.I)
_LOCK = re.compile(
    r"\b(?:locked in|confirmed|see you (?:then|there|on|at|\w+day)|in the calendar|"
    r"in the cal|it'?s booked|added it|calendar invite)\b", re.I)


def _conversations(msgs: list[Message]) -> list[list[Message]]:
    out: list[list[Message]] = []
    for m in msgs:
        if out and m.ts - out[-1][-1].ts <= CONVO_GAP:
            out[-1].append(m)
        else:
            out.append([m])
    return out


# A day can be named in order to rule it out: "not friday, how about saturday".
# Taking the first day in the message put the plan on the day that was refused.
_TURNED_DOWN = re.compile(
    r"\b(?:not|can'?t|cannot|won'?t|doesn'?t work|does not work|no good|rather not|"
    r"busy|unavailable|bad for me)\b", re.I)
_LIST_JOIN = re.compile(r"(?:\s|,|/|\bor\b|\band\b)*", re.I)


def _names_a_day(m: "re.Match") -> bool:
    """Is this match actually about when something happens?

    "How's your week" is a greeting, and it was the only way a bare "week" ever
    appeared in the demo corpus -- 221 times, every one of them small talk. Read as
    a date it put plans on a Saturday nobody had named. A week is only a timing
    expression when someone says which one.
    """
    return m.group(2).lower() != "week" or bool(m.group(1))


def _wanted_day(text: str) -> "re.Match | None":
    """The first day in the message that nobody has just ruled out.

    A refusal that trails a day belongs to that day, so "friday doesn't work,
    saturday?" leaves saturday. A refusal that opens the message carries across a
    list, so "can't do friday or saturday" rules out both -- but only across a plain
    "or"/"and"; "can't do monday but tuesday works" turns on the "but" and leaves
    tuesday. If every day named is ruled out, the first is still returned: the
    message is about those days even when the answer is no.
    """
    days = [m for m in _DAY.finditer(text) if _names_a_day(m)]
    if len(days) <= 1:
        return days[0] if days else None
    opens_refused = bool(_TURNED_DOWN.search(text[:days[0].start()]))
    carried = opens_refused
    for i, day in enumerate(days):
        if i:
            gap = text[days[i - 1].end():day.start()]
            if not _LIST_JOIN.fullmatch(gap):
                carried = False                  # a new clause, not another item
        trail = text[day.end():days[i + 1].start() if i + 1 < len(days) else len(text)]
        if not carried and not _TURNED_DOWN.search(trail):
            return day
        if _TURNED_DOWN.search(trail):
            carried = False                      # that refusal was about this day only
    return days[0]


def _resolve_date(text: str, anchor: datetime) -> tuple[str | None, datetime | None]:
    dm = _wanted_day(text)
    if not dm:
        return None, None
    phrase = dm.group(0).strip().lower()
    qualifier, token = (dm.group(1) or "").lower(), dm.group(2).lower()

    if token == "tonight":
        return phrase, anchor.replace(hour=20, minute=0, second=0, microsecond=0)
    if token == "tomorrow":
        return phrase, (anchor + timedelta(days=1)).replace(hour=19, minute=0, second=0, microsecond=0)
    if token == "week":
        # "this week" is a real answer about timing and not a day, so it is worth
        # keeping as text and worth refusing to turn into one. Guessing a Saturday
        # here fed invented dates to the plan states and to the day statistics.
        return phrase, None
    if token == "weekend":
        days = (5 - anchor.weekday()) % 7 or 7
        if qualifier == "next":
            days += 7
        return phrase, (anchor + timedelta(days=days)).replace(hour=19, minute=0, second=0, microsecond=0)
    if token in WEEKDAYS:
        days = (WEEKDAYS.index(token) - anchor.weekday()) % 7 or 7
        if qualifier == "next":
            days += 7
        return phrase, (anchor + timedelta(days=days)).replace(hour=19, minute=0, second=0, microsecond=0)

    num = re.match(r"(\d{1,2})", token)
    if num:                                   # "the 14th"
        day = int(num.group(1))
        for month_offset in (0, 1, 2):
            month = anchor.month + month_offset
            year = anchor.year + (month - 1) // 12
            month = (month - 1) % 12 + 1
            try:
                cand = anchor.replace(year=year, month=month, day=day,
                                      hour=19, minute=0, second=0, microsecond=0)
            except ValueError:
                continue
            if cand >= anchor - timedelta(days=1):
                return phrase, cand
    return phrase, None


def _clock(text: str) -> tuple[int, int] | None:
    """The hour and minute a time expression names, or None if it names none.

    A bare "6:30" is read as the evening. Plans skew that way, and the alternative
    is what this used to do: drop the time on the floor and call every plan 7pm.
    """
    s = text.strip().lower()
    if s == "noon":
        return 12, 0
    if s == "midnight":
        return 0, 0
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", s)
    if not m:
        return None
    hour, minute, meridiem = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if hour > 23 or minute > 59:
        return None
    if meridiem == "pm":
        hour = hour % 12 + 12
    elif meridiem == "am":
        hour = hour % 12
    elif hour <= 11:
        hour += 12                      # "6:30" about dinner is half six, not dawn
    return hour, minute


def _title(activity: str, thread_name: str) -> str:
    a = activity.strip().lower()
    label = a if a[0].isupper() else a.capitalize()
    return f"{label} with {thread_name}"


def extract_plans(corpus: Corpus, now: datetime | None = None) -> list[Plan]:
    now = now or datetime.now()
    plans: list[Plan] = []
    counter = 0

    for thread in corpus.threads.values():
        msgs = corpus.messages_in(thread.id)
        for convo in _conversations(msgs):
            seeds: list[tuple[int, str]] = []
            for i, m in enumerate(convo):
                act = _ACT.search(m.text)
                if not act:
                    continue
                if INTENT.search(m.text) or _wanted_day(m.text):
                    seeds.append((i, act.group(1).lower()))

            seen_acts: set[str] = set()
            seed_positions = [i for i, _ in seeds]
            for idx, activity in seeds:
                if activity in seen_acts:
                    continue
                seen_acts.add(activity)
                # Stop at the next plan seed, and cap the window: agreement voiced
                # ten messages later is about whatever the conversation moved on to,
                # not about this plan.
                nxt = next((i for i in seed_positions if i > idx), None)
                end = min(nxt if nxt is not None else len(convo), idx + 9)
                window = convo[idx:max(end, idx + 2)]
                counter += 1

                when_text, when_date, said_time = None, None, None
                yes_others = no_others = maybe_others = 0
                locked = False
                for m in window:
                    if when_date is None:
                        wt, wd = _resolve_date(m.text, m.ts)
                        if wt:
                            when_text, when_date = wt, wd
                    # The time can be named before the day ("8pm?" ... "friday works"),
                    # so it is collected on its own and applied once the window is read.
                    if said_time is None:
                        hit = _TIME.search(m.text)
                        if hit:
                            said_time = hit.group(0)
                    if _LOCK.search(m.text):
                        locked = True
                    if m is window[0] or m.is_from_me == window[0].is_from_me:
                        continue          # only the other side's answer settles a plan
                    if _NO.search(m.text):
                        no_others += 1
                    elif _YES.search(m.text):
                        yes_others += 1
                    elif _MAYBE.search(m.text):
                        maybe_others += 1

                if said_time and when_text and said_time.lower() not in when_text.lower():
                    when_text = f"{when_text} {said_time}"
                if said_time and when_date is not None:
                    hm = _clock(said_time)
                    if hm:
                        when_date = when_date.replace(hour=hm[0], minute=hm[1])

                last = window[-1].ts
                idle = now - last

                if no_others and yes_others == 0:
                    state = "dropped"
                elif no_others and not locked:
                    state = "dropped"
                elif locked and when_date:
                    state = "done" if when_date < now else "scheduled"
                elif yes_others >= 1 and when_text:
                    state = "done" if (when_date and when_date < now and locked) else "confirmed"
                elif when_text:
                    state = "proposed"
                else:
                    state = "floated"

                if state in ("floated", "proposed") and idle > STALE_AFTER:
                    state = "stale"
                assumed = False
                if state == "confirmed" and when_date and when_date < now - timedelta(days=2):
                    # Agreed to, dated, and the date went by. That is not the same as
                    # knowing it happened -- nobody said so -- so it is marked as the
                    # guess it is, and anything that would speak for you leaves it alone.
                    state, assumed = "done", True

                confidence = 0.45
                if INTENT.search(window[0].text):
                    confidence += 0.2
                if when_text:
                    confidence += 0.2
                if yes_others or no_others:
                    confidence += 0.1
                if locked:
                    confidence += 0.05

                plans.append(Plan(
                    id=f"plan{counter:04d}", title=_title(activity, thread.name),
                    thread_id=thread.id, thread_name=thread.name,
                    participant_ids=list(thread.participant_ids),
                    state=state, confidence=round(min(1.0, confidence), 2),
                    first_seen=window[0].ts, last_activity=last,
                    when_text=when_text, when_date=when_date,
                    evidence=[m.id for m in window[:8]],
                    excerpt=window[0].text[:160], assumed=assumed,
                ))

    return _merge(plans)


def _merge(plans: list[Plan]) -> list[Plan]:
    """Collapse repeat mentions of the same activity in the same thread.

    'we should get dinner' said three times across a month is one unlanded plan,
    not three -- and counting it as three would flatter the follow-through stats.
    """
    rank = {"dropped": 0, "floated": 1, "stale": 2, "proposed": 3,
            "confirmed": 4, "scheduled": 5, "done": 6}
    by_key: dict[tuple[str, str], Plan] = {}
    out: list[Plan] = []
    for p in sorted(plans, key=lambda x: x.first_seen):
        key = (p.thread_id, p.title)
        prev = by_key.get(key)
        if prev is not None and p.first_seen - prev.last_activity <= MERGE_WINDOW:
            prev.last_activity = max(prev.last_activity, p.last_activity)
            prev.evidence = (prev.evidence + p.evidence)[:12]
            if rank[p.state] > rank[prev.state]:
                prev.state, prev.when_text, prev.when_date = p.state, p.when_text, p.when_date
            prev.confidence = max(prev.confidence, p.confidence)
            continue
        by_key[key] = p
        out.append(p)
    out.sort(key=lambda p: p.last_activity, reverse=True)
    return out


OPEN_STATES = {"floated", "proposed", "confirmed", "scheduled"}


def open_plan_counts(plans: list[Plan]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for p in plans:
        if p.state in OPEN_STATES:
            for cid in p.participant_ids:
                counts[cid] = counts.get(cid, 0) + 1
    return counts


def follow_through(plans: list[Plan]) -> dict[str, float]:
    """Per contact: of the plans that got far enough to judge, what share landed.

    "Landed" means a date was actually locked, not merely agreed to. Saying yes and
    then never picking a day is the pattern this is here to catch, so `confirmed`
    counts as a miss rather than a hit.
    """
    landed: dict[str, int] = {}
    total: dict[str, int] = {}
    for p in plans:
        if p.state == "floated":
            continue                      # never got far enough to judge
        for cid in p.participant_ids:
            total[cid] = total.get(cid, 0) + 1
            if p.state in ("scheduled", "done"):
                landed[cid] = landed.get(cid, 0) + 1
    return {cid: landed.get(cid, 0) / n for cid, n in total.items() if n >= 3}
