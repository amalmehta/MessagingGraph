"""How numbers are said out loud.

Rounding a rate into a sentence is where this app says something no person
would. "You used to talk about 1 times a week" shipped and was visible on a
real history the first time anyone looked at one -- the synthetic people all
talk often enough that `:.0f` never landed on 1, so nothing caught it.
"""
from __future__ import annotations


def a_week(rate: float) -> str:
    """A weekly rate, said the way someone would say it.

    Reads after the word "about": "You used to talk about once a week."
    Guards both ends -- 1.2 rounding down to "1 times", and a rate under half
    rounding to "0 times a week", which says the opposite of what it means.
    """
    if rate < 0.75:
        return "every couple of weeks"
    if rate < 1.5:
        return "once a week"
    if rate < 2.5:
        return "twice a week"
    # Not :.0f. Python rounds halves to even, so 2.5 came back out of this
    # branch as "2 times a week" -- contradicting the line directly above it.
    return f"{int(rate + 0.5)} times a week"


def count(n: int, singular: str, plural: str | None = None) -> str:
    """A number and its noun, agreeing. `count(1, "day")` -> "1 day"."""
    return f"{n} {singular if n == 1 else (plural or singular + 's')}"


def rate(value: float, singular: str, plural: str | None = None) -> str:
    """The same, for a number that has to be rounded on the way in.

    int(value + 0.5) rather than :.0f, which rounds halves to even and so
    turns 2.5 into 2 -- the rounding is what produced "1 times a week".
    """
    return count(int(value + 0.5), singular, plural)


def gap_words(days: int) -> str:
    """How long it's been, the way you'd say it to the person themselves.

    Reads after "it's been": "it's been the better part of a year."
    """
    if days < 21:
        return count(days // 7 or 1, "week")
    if days < 45:
        return "a few weeks"
    if days < 60:
        return "over a month"
    if days < 300:
        return f"{max(2, round(days / 30.4))} months"
    if days < 420:
        return "the better part of a year"
    # int() on 1.9 years gave "over 1 years". Anything short of two is a year.
    years = int(days / 365.25)
    return "over a year" if years < 2 else f"over {years} years"


MONTHS = ("January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December")


def when_words(then, now) -> str:
    """When something happened, said the way you would say it to someone.

    Reads after "it's been" or on its own: "back in August", "last November".
    The year comes from the calendar, not from a day count: at 330 days the old
    rule called November 2025 "back in November" while standing in October
    2026, which is last November by any reading.
    """
    days = (now - then).days
    if days < 21:
        return "the other week" if days >= 10 else "the other day"
    if days < 56:
        return f"about {max(3, round(days / 7))} weeks ago"
    month = MONTHS[then.month - 1]
    return f"back in {month}" if then.year == now.year else f"last {month}"
