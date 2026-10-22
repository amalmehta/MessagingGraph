"""Birthdays and anniversaries, read out of what people actually said.

Two kinds of evidence, both plain text and timestamps, so it works the same on
Messages and WhatsApp and never leaves this Mac:

1. **A wish, on the day.** "happy birthday!" sent on 3 March says the birthday is
   3 March. Who it is for depends on where it was said: in a one-to-one thread a
   wish from me is for them (one from them is for me, and not recorded); in a
   group it has to name someone, or be answered by the one person thanking
   everybody. A wish that can't be pinned on one person is dropped, not guessed.
2. **Someone saying so.** "my birthday is March 3", "it's my birthday today",
   "turning 30 tomorrow". Their own words beat anyone else's wish.

The same date turning up again a year later is what makes a reading trustworthy,
so confidence is built per year: wishes on the same day in one year are one
piece of evidence, not five, and each further year that agrees adds to it.
"belated" wishes say nothing about the date and are ignored, as are wishes to
somebody's mum, numeric dates (3/4 is March or April depending on who wrote
it), and text I pasted in.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from ..models import Corpus, Message, SELF_ID
from ..names import short_name
from .phrasing import MONTHS
from .quoted import is_quoted, my_name

KINDS = ("birthday", "anniversary", "work_anniversary")
LABEL = {"birthday": "birthday", "anniversary": "anniversary", "work_anniversary": "work anniversary"}

# Weight of one piece of evidence: how likely it alone is to be right.
SAID = 0.7              # "my birthday is March 3"
SAID_TODAY = 0.65       # "it's my birthday today" -- same, but the day is read off the timestamp
ASKED = 0.45            # me: "your birthday is March 3 right?"
WISHED_DM = 0.5         # me, in a one-to-one thread
THANKED = 0.15          # ...and they said thanks the same day
WISHED_GROUP = 0.45     # a group wish that names them
THANKER = 0.4           # a group wish answered by them thanking everyone
PER_WISHER = 0.05       # each further person in the group wishing the same day
MAX_CONFIDENCE = 0.97
NEAR_DAYS = 2           # wishes a day late, or a statement a day off, are the same date
DISMISS_NEAR = 3        # a dismissed date keeps nearby readings away too

_MON = {m.lower(): i + 1 for i, m in enumerate(MONTHS)}
_MON.update({m.lower()[:3]: i + 1 for i, m in enumerate(MONTHS)})
_MON["sept"] = 9
_MONTH = r"(?:" + "|".join(sorted(_MON, key=len, reverse=True)) + r")"
_ORD = r"(?:st|nd|rd|th)?"
DATE = (rf"(?:(?P<m1>{_MONTH})\.?\s+(?:the\s+)?(?P<d1>\d{{1,2}}){_ORD}\b"
        rf"|(?:the\s+)?(?P<d2>\d{{1,2}}){_ORD}\s+(?:of\s+)?(?P<m2>{_MONTH})\b)")
_BDAY = r"(?:birthday|bday|b-day|birth day)"
_ANNIV = r"(?:(?P<work>work\s+)?anniversary|anniv)"
_IS = r"(?:'s|’s|\s+is|\s+was|\s+will\s+be)?(?:\s+on)?\s+"

WISH = re.compile(
    rf"\b(?:happy|hppy|hapy)\s+(?:(?P<age>\d{{1,3}})(?:st|nd|rd|th)?[\s-]+(?:years?[\s-]+)?)?(?:(?P<b>{_BDAY})|{_ANNIV})"
    rf"|\b(?P<hbd>hbd)\b", re.I)
BELATED = re.compile(r"\b(?:belated|late|missed|forgot|yesterday)\b", re.I)
# "did you wish sam happy birthday" is reporting a wish, not making one.
REPORTED = re.compile(r"\b(?:say|said|wish|wished|tell|told|text|texted|sent|send|card|post|write|wrote)\b"
                      r"[^.!?\n]{0,25}\bhappy\s", re.I)
# What follows "happy birthday to": you, me, a name, or somebody's somebody.
TO = re.compile(r"[\s,!]*(?P<to>to\s+)?@?(?P<who>[a-z][\w'’]*)?", re.I)
YOU = {"you", "u", "ya", "yaa", "youu", "yall", "y'all", "babe", "bestie"}
OTHERS = {"your", "ur", "his", "her", "their", "the", "my", "our", "little", "lil"}

STATED = re.compile(rf"\b(?P<whose>my|our|your|ur)\s+(?:(?P<b>{_BDAY})|(?:wedding\s+)?{_ANNIV}){_IS}{DATE}", re.I)
STATED_AFTER = re.compile(rf"{DATE}\s+(?:is|was)\s+(?P<whose>my|our|your|ur)\s+(?:(?P<b>{_BDAY})|(?:wedding\s+)?{_ANNIV})", re.I)
RELATIVE = re.compile(
    rf"\b(?:(?:it'?s|its|it’s)\s+my\s+(?P<b1>{_BDAY})\s*(?P<r1>today|tomorrow)?"
    rf"|(?P<r2>today|tomorrow|yesterday)\s+(?:is|was)\s+my\s+(?P<b2>{_BDAY})"
    rf"|my\s+(?P<b3>{_BDAY})(?:'s|’s|\s+is|\s+was)\s+(?P<r3>today|tomorrow|yesterday)"
    rf"|\bi'?m\s+turning\s+(?P<age>\d{{1,3}})\s+(?P<r4>today|tomorrow))\b", re.I)
NOT_NOW = re.compile(r"\b(?:not|isn'?t|wasn'?t|if|when|was it|is it)\b[^.!?\n]{0,12}$", re.I)
THANKS_BACK = re.compile(r"^\W*(?:thank|thanks|thx|ty|tysm|aw+|ah+|love (?:you|u)|you guys|y'?all are)", re.I)
SHIFT = {"today": 0, "tomorrow": 1, "yesterday": -1, None: 0}
FILTER = ("birth", "bday", "b-day", "hbd", "anniv", "turning")


@dataclass
class Evidence:
    contact_id: str
    kind: str
    month: int
    day: int
    weight: float
    source: str              # said | wished
    msg: Message
    birth_year: int | None = None


@dataclass
class Milestone:
    id: str
    contact_id: str
    name: str
    kind: str                # birthday | anniversary | work_anniversary
    month: int
    day: int
    confidence: float        # 0..1
    years: list[int]         # distinct years the date was seen in
    why: str
    source: dict             # the message that says it most plainly
    evidence: list[str] = field(default_factory=list)   # message ids
    birth_year: int | None = None


def milestone_id(kind: str, contact_id: str, month: int, day: int) -> str:
    return f"{kind}:{contact_id}:{month:02d}-{day:02d}"


def _md(month: int, day: int) -> int:
    """Day of a leap year, so 29 February has a place and distances wrap at New Year."""
    return (date(2000, month, day) - date(2000, 1, 1)).days


def day_gap(a: tuple[int, int], b: tuple[int, int]) -> int:
    d = abs(_md(*a) - _md(*b))
    return min(d, 366 - d)


def _valid(month: int, day: int) -> bool:
    try:
        date(2000, month, day)
    except ValueError:
        return False
    return True


def _date_of(hit: re.Match) -> tuple[int, int] | None:
    m = hit.group("m1") or hit.group("m2")
    d = hit.group("d1") or hit.group("d2")
    if not m or not d:
        return None
    month, day = _MON[m.lower().rstrip(".")], int(d)
    return (month, day) if _valid(month, day) else None


def _kind(hit: re.Match) -> str:
    if hit.groupdict().get("b") or hit.groupdict().get("hbd"):
        return "birthday"
    return "work_anniversary" if hit.groupdict().get("work") else "anniversary"


def _first_names(corpus: Corpus, ids) -> dict[str, str]:
    """first name (lowercased) -> contact id, for names only one person in the list has."""
    seen: dict[str, list[str]] = {}
    for cid in ids:
        c = corpus.contacts.get(cid)
        if c is None:
            continue
        first = short_name(c.name).lower()
        if first and first.isalpha() and len(first) > 1:
            seen.setdefault(first, []).append(cid)
    return {k: v[0] for k, v in seen.items() if len(v) == 1}


def _named(text: str, names: dict[str, str], me: str | None) -> list[str]:
    words = {w.lower() for w in re.findall(r"[a-z]+", text, re.I)}
    if me and me in words:
        return []
    return [cid for first, cid in names.items() if first in words]


def _wishes(corpus: Corpus, me: str | None) -> list[Evidence]:
    out: list[Evidence] = []
    for thread in corpus.threads.values():
        msgs = corpus.messages_in(thread.id)
        names = _first_names(corpus, thread.participant_ids)
        for i, m in enumerate(msgs):
            if not m.text or not any(f in m.text.lower() for f in FILTER):
                continue
            hit = WISH.search(m.text)
            if not hit or BELATED.search(m.text) or REPORTED.search(m.text):
                continue
            if m.is_from_me and is_quoted(m.text, me):
                continue
            kind = _kind(hit)
            age = int(hit.group("age")) if hit.group("age") else None
            to = TO.match(m.text, hit.end())
            who = (to.group("who") or "").lower()
            if who in OTHERS:
                continue                      # "happy birthday to your mum"
            if who == "me":
                # "happy birthday to me": the sender's own.
                if m.sender_id != SELF_ID:
                    out.append(_ev(m.sender_id, kind, m, SAID_TODAY, "said", age))
                continue
            same_day = [x for x in msgs[max(0, i - 40):i + 40]
                        if x.ts.date() == m.ts.date() and x is not m]
            if not thread.is_group:
                if not m.is_from_me:
                    continue                  # them wishing me
                cid = thread.participant_ids[0]
                if to.group("to") and who not in YOU and names.get(who) != cid:
                    continue                  # "happy birthday to Leo": someone else in their life
                thanked = any(x.sender_id == cid and x.ts > m.ts and THANKS_BACK.search(x.text or "")
                              for x in same_day)
                out.append(_ev(cid, kind, m, WISHED_DM + (THANKED if thanked else 0), "wished", age))
                continue
            # A group: say who, or be answered by the one person thanking everybody.
            named = [c for c in _named(m.text, names, me) if c != m.sender_id]
            if kind == "birthday" and len(named) > 1:
                continue
            wishers = {x.sender_id for x in same_day if WISH.search(x.text or "")} | {m.sender_id}
            if not named:
                thankers = {x.sender_id for x in same_day
                            if x.ts > m.ts and x.sender_id not in wishers
                            and THANKS_BACK.search(x.text or "")}
                if len(thankers) != 1 or SELF_ID in thankers:
                    continue                  # nobody, several people, or me
                named, base = list(thankers), THANKER
            else:
                base = WISHED_GROUP
            bonus = PER_WISHER * min(4, len(wishers) - 1)
            for cid in named:
                out.append(_ev(cid, kind, m, base + bonus, "wished", age))
    return out


def _ev(cid: str, kind: str, m: Message, weight: float, source: str, age: int | None = None,
        shift: int = 0) -> Evidence:
    d = (m.ts + timedelta(days=shift)).date()
    year = d.year - age if age and kind == "birthday" and 0 < age < 120 else None
    return Evidence(cid, kind, d.month, d.day, weight, source, m, year)


def _statements(corpus: Corpus, me: str | None) -> list[Evidence]:
    out: list[Evidence] = []
    for thread in corpus.threads.values():
        for m in corpus.messages_in(thread.id):
            text = m.text or ""
            low = text.lower()
            if not any(f in low for f in FILTER):
                continue
            if m.is_from_me and is_quoted(text, me):
                continue
            for pat in (STATED, STATED_AFTER):
                for hit in pat.finditer(text):
                    if NOT_NOW.search(text[:hit.start()]):
                        continue
                    md = _date_of(hit)
                    if md is None:
                        continue
                    whose, kind = hit.group("whose").lower(), _kind(hit)
                    if whose == "my" and not m.is_from_me:
                        cid, w = m.sender_id, SAID
                    elif whose in ("your", "ur") and m.is_from_me and not thread.is_group:
                        cid, w = thread.participant_ids[0], ASKED
                    elif whose == "our" and kind != "birthday" and not thread.is_group:
                        # Our anniversary, in a one-to-one thread: the two of us.
                        cid, w = thread.participant_ids[0], SAID
                    else:
                        continue
                    out.append(Evidence(cid, kind, md[0], md[1], w, "said", m))
            if m.is_from_me:
                continue
            for hit in RELATIVE.finditer(text):
                if NOT_NOW.search(text[:hit.start()]):
                    continue
                rel = next((hit.group(g) for g in ("r1", "r2", "r3", "r4") if hit.group(g)), None)
                age = int(hit.group("age")) if hit.group("age") else None
                out.append(_ev(m.sender_id, "birthday", m, SAID_TODAY, "said", age, SHIFT[rel.lower() if rel else None]))
    return out


def _why(name: str, kind: str, ev: list[Evidence], years: list[int]) -> str:
    first = short_name(name)
    label = LABEL[kind]
    said = [e for e in ev if e.source == "said" and not e.msg.is_from_me]
    if said:
        line = f"{first} mentioned their {label}"
    elif any(e.msg.is_from_me for e in ev):
        line = f"You wished {first} a happy {label} on this day"
    else:
        line = f"{first} got happy-{label} wishes on this day"
    if len(years) > 1:
        line += " in " + ", ".join(map(str, years[:-1])) + f" and {years[-1]}"
    elif years:
        line += f" in {years[0]}"
    return line + "."


def detect(corpus: Corpus) -> list[Milestone]:
    """Every birthday and anniversary the history supports, one per person and kind."""
    me = my_name(corpus)
    found = [e for e in _wishes(corpus, me) + _statements(corpus, me)
             if e.contact_id in corpus.contacts and e.contact_id != SELF_ID]
    groups: dict[tuple[str, str], list[Evidence]] = {}
    for e in found:
        groups.setdefault((e.contact_id, e.kind), []).append(e)

    out: list[Milestone] = []
    for (cid, kind), ev in groups.items():
        # The date most of the evidence agrees with, give or take a late wish.
        def support(md):
            near = [e for e in ev if day_gap((e.month, e.day), md) <= NEAR_DAYS]
            exact = sum(e.weight for e in ev if (e.month, e.day) == md)
            said = any(e.source == "said" and (e.month, e.day) == md for e in ev)
            return (_combine(near), said, exact, max(e.msg.ts for e in near))
        best = max({(e.month, e.day) for e in ev}, key=support)
        near = [e for e in ev if day_gap((e.month, e.day), best) <= NEAR_DAYS]
        conf = _combine(near)
        # Another date with real evidence behind it makes this one less certain.
        total = sum(e.weight for e in ev)
        conf *= sum(e.weight for e in near) / total
        years = sorted({e.msg.ts.year for e in near})
        lead = max(near, key=lambda e: (e.source == "said", e.weight, e.msg.ts))
        thread = corpus.threads.get(lead.msg.thread_id)
        sender = ("You" if lead.msg.is_from_me
                  else corpus.contacts[lead.msg.sender_id].name if lead.msg.sender_id in corpus.contacts
                  else "Someone")
        births = [e.birth_year for e in near if e.birth_year]
        name = corpus.contacts[cid].name
        out.append(Milestone(
            id=milestone_id(kind, cid, *best), contact_id=cid, name=name, kind=kind,
            month=best[0], day=best[1], confidence=round(min(MAX_CONFIDENCE, conf), 3),
            years=years, why=_why(name, kind, near, years),
            source={"message_id": lead.msg.id, "thread_id": lead.msg.thread_id,
                    "thread": thread.name if thread else "", "group": bool(thread and thread.is_group),
                    "sender": sender, "ts": lead.msg.ts.isoformat(), "text": (lead.msg.text or "")[:200]},
            evidence=[e.msg.id for e in sorted(near, key=lambda e: e.msg.ts)],
            birth_year=max(set(births), key=births.count) if births else None))
    out.sort(key=lambda m: (-m.confidence, m.name))
    return out


def _combine(ev: list[Evidence]) -> float:
    """One vote per year and kind of evidence; independent votes add up.

    Five friends wishing the same afternoon is one observation. The same day a
    year later, or them saying so themselves, is another.
    """
    best: dict[tuple[int, str], float] = {}
    for e in ev:
        k = (e.msg.ts.year, e.source)
        best[k] = max(best.get(k, 0.0), min(e.weight, 0.9))
    miss = 1.0
    for w in best.values():
        miss *= 1 - w
    return 1 - miss


def next_date(month: int, day: int, today: date) -> date:
    """The next time this month and day comes round, today included. 29 February
    falls on the 28th in other years rather than skipping them."""
    for year in (today.year, today.year + 1):
        d = day if _valid_in(year, month, day) else 28
        when = date(year, month, d)
        if when >= today:
            return when
    return date(today.year + 1, month, day if _valid_in(today.year + 1, month, day) else 28)


def _valid_in(year: int, month: int, day: int) -> bool:
    try:
        date(year, month, day)
    except ValueError:
        return False
    return True


def dismissed(m: dict | Milestone, verdicts: dict[str, dict]) -> bool:
    """True when you've said no to this date for this person, or one a few days from it."""
    kind, cid = (m["kind"], m["contact_id"]) if isinstance(m, dict) else (m.kind, m.contact_id)
    md = (m["month"], m["day"]) if isinstance(m, dict) else (m.month, m.day)
    for vid, v in verdicts.items():
        if v.get("status") != "dismissed":
            continue
        try:
            vkind, vcid, vmd = vid.split(":", 2)
            vm, vd = (int(x) for x in vmd.split("-"))
        except ValueError:
            continue
        if vkind == kind and vcid == cid and day_gap((vm, vd), md) <= DISMISS_NEAR:
            return True
    return False


def upcoming(items: list[dict], verdicts: dict[str, dict], today: date, days: int = 30) -> list[dict]:
    """What falls in the next `days`, soonest first, with nothing you've dismissed.

    A date you've confirmed stands in for every other reading of the same kind
    for that person.
    """
    confirmed = {(i["contact_id"], i["kind"]) for i in items
                 if verdicts.get(i["id"], {}).get("status") == "confirmed"}
    out = []
    for i in items:
        status = verdicts.get(i["id"], {}).get("status")
        if status != "confirmed" and ((i["contact_id"], i["kind"]) in confirmed or dismissed(i, verdicts)):
            continue
        when = next_date(i["month"], i["day"], today)
        until = (when - today).days
        if until > days:
            continue
        out.append({**i, "status": status or "detected", "date": when.isoformat(), "days_until": until})
    out.sort(key=lambda i: (i["days_until"], -i["confidence"]))
    return out
