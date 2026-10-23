"""The weekly digest: a handful of things, not the whole list.

Everything else on the Now page is a ranked list, and a ranked list shows the
same names at the top every time you open it. This picks a small set -- up to
three people you've drifted from, two plans that never got a date and one
message worth sending -- and turns the set over each week, so a long tail of
people gets its turn instead of the same three being raised every Monday.

Snoozes live here too, because they decide what the digest is allowed to pick
from. A snooze is a choice about a person or a plan ("not this month"), and it
outlasts a re-import: people are keyed by their stable ids, plans by the
thread and the activity rather than by the counter that numbers them on each
pass. Stored with your other corrections, sealed in the vault.

Nothing here reads the network or the wall clock on its own: `wall` and `now`
are passed in, so the rotation and the expiry can be checked on any date.
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime, time, timedelta

from ..models import Plan, RelationshipProfile
from .circles import activity_of
from .phrasing import a_week, count, when_words
from .plans import OPEN_STATES

PEOPLE, PLANS, MESSAGES = 3, 2, 1
# How far down each ranking the rotation reaches. Three weeks of people and
# three of plans before anyone comes round again; further down than that the
# ranking stops meaning much.
POOL_PEOPLE, POOL_PLANS, POOL_MESSAGES = 9, 6, 4
DRIFTING = ("dormant", "fading")
# Below this the app is guessing about a thread (the same line the People
# table folds away), and a guess is not worth one of three slots.
THIN = 0.35

SNOOZE_DAYS = {"week": 7, "month": 30, "forever": None}
SNOOZE_WORDS = {"week": "for a week", "month": "for a month", "forever": "for good"}
# Drafts that exist because someone has drifted or a plan has stalled. A
# snoozed person still gets a reply drafted when they ask you something:
# snoozing is "stop nudging me about them", not "stop hearing them".
DRIFT_DRAFTS = ("reconnect", "check_in", "revive_plan")

# How long after the chosen time a notification may still go out. A Mac that
# was asleep at nine on Monday morning catches up when it wakes at ten, not
# on Thursday, by which point "this week" is half gone.
NOTIFY_GRACE = timedelta(hours=12)
DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


# ------------------------------------------------------------------ the week
def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def week_number(day: date) -> int:
    """Weeks since a fixed Monday, so consecutive weeks are consecutive numbers
    across a year boundary (ISO week 52 is followed by week 1)."""
    return week_start(day).toordinal() // 7


def week_key(day: date) -> str:
    y, w, _ = day.isocalendar()
    return f"{y}-W{w:02d}"


def rotate(items: list, k: int, week: int) -> list:
    """k items from a ranked list, turned over by one window each week.

    The window starts k further along each week and wraps, so with at least
    2k items two weeks in a row never share one, and every item turns up
    within ceil(n / k) weeks. Ranked order is kept inside the window.
    """
    n = len(items)
    if n <= k:
        return list(items)
    start = (week * k) % n
    picked = sorted((start + i) % n for i in range(k))
    return [items[i] for i in picked]


# ------------------------------------------------------------------ snoozes
def plan_key(plan: Plan) -> str:
    """A name for a plan that survives a recompute. Plan ids are a counter
    (plan0001) handed out on each pass, so a snooze keyed by one would land
    on a different plan the next time anything changed."""
    raw = f"{plan.thread_id}|{activity_of(plan).strip().lower()}"
    return "plan:" + hashlib.blake2b(raw.encode(), digest_size=8).hexdigest()


def person_key(contact_id: str) -> str:
    return "person:" + contact_id


def snooze_until(length: str, now: datetime) -> datetime | None:
    """When a snooze of this length runs out; None for one that never does."""
    if length not in SNOOZE_DAYS:
        raise ValueError(f"unknown snooze {length!r}")
    days = SNOOZE_DAYS[length]
    return None if days is None else now + timedelta(days=days)


def is_active(entry: dict, now: datetime) -> bool:
    until = entry.get("until")
    if until is None:
        return True
    try:
        return datetime.fromisoformat(until) > now
    except (TypeError, ValueError):
        return False            # unreadable: let it lapse rather than hide something for ever


def live(snoozes: dict[str, dict], now: datetime) -> dict[str, dict]:
    """The snoozes still running. Expired ones are simply not here."""
    return {k: v for k, v in snoozes.items() if isinstance(v, dict) and is_active(v, now)}


# ------------------------------------------------------------------ picking
def drifted(profiles: list[RelationshipProfile], snoozed: set[str] | frozenset = frozenset()
            ) -> list[RelationshipProfile]:
    """Who has dropped furthest below their own usual rate, worst first."""
    gone = [p for p in profiles
            if p.trend in DRIFTING and p.msgs_per_week > 0 and p.state not in ("new", "group")
            and p.confidence >= THIN and person_key(p.contact_id) not in snoozed]
    return sorted(gone, key=lambda p: (p.msgs_per_week_recent / p.msgs_per_week, p.name))


def undated(plans: list[Plan], snoozed: set[str] | frozenset = frozenset()) -> list[Plan]:
    """Plans that were wanted and never got a day, most recently raised first."""
    open_ = [p for p in plans
             if (p.state == "stale" or p.state in OPEN_STATES) and p.when_date is None
             and plan_key(p) not in snoozed]
    return sorted(open_, key=lambda p: (p.last_activity, p.id), reverse=True)


def _person_line(p: RelationshipProfile, now: datetime) -> str:
    since = (now - p.last_contact).days if p.last_contact else 0
    return (f"You used to talk about {a_week(p.msgs_per_week)}. "
            f"Last message {count(since, 'day')} ago.")


def _plan_line(p: Plan, now: datetime) -> str:
    return f"Came up {when_words(p.last_activity, now)} and never got a date."


def build(profiles: list[RelationshipProfile], plans: list[Plan], drafts: list,
          snoozes: dict[str, dict], wall: datetime, now: datetime,
          hidden_drafts: set[str] | frozenset = frozenset(),
          names: dict[str, str] | None = None) -> dict:
    """This week's digest.

    `now` is the data's clock (the newest message), for how long ago things
    were; `wall` is the calendar, for which week it is and which snoozes have
    run out. They differ for anyone who imported a while ago.
    """
    asleep = set(live(snoozes, wall))
    today = wall.date()
    week = week_number(today)
    names = names or {}

    people = rotate(drifted(profiles, asleep)[:POOL_PEOPLE], PEOPLE, week)
    stalled = rotate(undated(plans, asleep)[:POOL_PLANS], PLANS, week)
    messages = [d for d in drafts if d.id not in hidden_drafts
                and not any(person_key(t) in asleep for t in d.target_ids)]
    message = rotate(messages[:POOL_MESSAGES], MESSAGES, week)

    out = {
        "week": week_key(today),
        "label": "Week of " + week_start(today).strftime("%-d %B"),
        "people": [{"id": p.contact_id, "name": p.name, "line": _person_line(p, now),
                    "trend": p.trend} for p in people],
        "plans": [{"id": p.id, "key": plan_key(p), "activity": activity_of(p),
                   "thread_name": p.thread_name,
                   "people": [names.get(c, "") for c in p.participant_ids if c in names],
                   "state": p.state, "line": _plan_line(p, now)} for p in stalled],
        "message": message[0] if message else None,
    }
    out["empty"] = not (out["people"] or out["plans"] or out["message"])
    out["empty_line"] = empty_line(bool(asleep)) if out["empty"] else ""
    out["notification"] = notification_line(len(people), len(stalled), bool(message))
    return out


# ------------------------------------------------------------------ wording
def empty_line(anything_snoozed: bool) -> str:
    """What the card says when there is nothing to put on it.

    Two different weeks: one where nothing has drifted, which is good news and
    should read like it, and one where the only things there are you snoozed,
    which should say so -- or the card claims all is well while the app is in
    fact holding things back at your request.
    """
    if anything_snoozed:
        return ("Nothing new this week. The rest is snoozed, and each one comes "
                "back on its own when its snooze runs out.")
    return ("Nothing to catch up on this week. Nobody you talk to has drifted, "
            "and every plan has a date.")


def notification_line(people: int, plans: int, message: bool) -> str | None:
    """The notification's text: counts and nothing else.

    No names. A notification shows on the lock screen and in Notification
    Centre, where anyone who picks up the Mac can read it, and who you've
    drifted from is exactly the sort of thing this app keeps to itself.
    None when there is nothing to say, so an empty week sends nothing.
    """
    parts = []
    if people:
        parts.append(f"{count(people, 'person', 'people')} you've drifted from")
    if plans:
        parts.append(f"{count(plans, 'plan')} without a date")
    if message:
        parts.append("a message worth sending")
    if not parts:
        return None
    text = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
    return text[0].upper() + text[1:] + "."


def snooze_note(name: str, length: str, until: datetime | None) -> str:
    if until is None:
        return f"{name} won't come up again until you bring them back."
    return f"{name} is snoozed until {until.strftime('%-d %B')}."


# ------------------------------------------------------------------ the notification
def clean_notify(raw: dict | None) -> dict:
    """The notification setting, with anything odd replaced by the default: off,
    Monday, nine in the morning."""
    raw = raw if isinstance(raw, dict) else {}
    weekday = raw.get("weekday", 0)
    weekday = weekday if isinstance(weekday, int) and 0 <= weekday <= 6 else 0
    at = str(raw.get("time", "09:00"))
    try:
        hh, mm = (int(x) for x in at.split(":"))
        if not (0 <= hh < 24 and 0 <= mm < 60):
            raise ValueError
        at = f"{hh:02d}:{mm:02d}"
    except ValueError:
        at = "09:00"
    return {"on": bool(raw.get("on", False)), "weekday": weekday, "time": at}


def notify_due(setting: dict, now: datetime, last_week: str | None) -> bool:
    """Whether the weekly notification should go out now.

    Due once the chosen day and time have come round this week, for as long
    as NOTIFY_GRACE after it, and only if this week's has not gone already.
    """
    s = clean_notify(setting)
    if not s["on"]:
        return False
    hh, mm = (int(x) for x in s["time"].split(":"))
    moment = datetime.combine(week_start(now.date()) + timedelta(days=s["weekday"]), time(hh, mm))
    return (moment <= now < moment + NOTIFY_GRACE) and last_week != week_key(now.date())

