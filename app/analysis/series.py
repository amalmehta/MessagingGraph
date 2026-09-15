"""Time bucketing for the messaging-rate charts.

Buckets are rolling 7-day windows anchored to *now* rather than to calendar
weeks, so the rightmost bucket is always "the last seven days" and lines up with
the 30-day window the trend reading is computed from.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from ..models import Corpus, Message

DEFAULT_WEEKS = 40


def bucket_series(msgs: list[Message], now: datetime,
                  weeks: int = DEFAULT_WEEKS) -> list[dict]:
    mine = [0] * weeks
    theirs = [0] * weeks
    for m in msgs:
        days = (now - m.ts).total_seconds() / 86400
        idx = weeks - 1 - int(max(0.0, days) // 7)
        if 0 <= idx < weeks:
            (mine if m.is_from_me else theirs)[idx] += 1

    out = []
    for i in range(weeks):
        start = now - timedelta(days=7 * (weeks - i))
        out.append({
            "start": start,
            "end": start + timedelta(days=7),
            "mine": mine[i],
            "theirs": theirs[i],
            "total": mine[i] + theirs[i],
        })
    return out


def series_summary(buckets: list[dict], first: datetime | None = None) -> dict:
    """Headline numbers the chart annotates itself with.

    The quarter-on-quarter change is withheld unless the history covers at least
    half of that earlier quarter. Otherwise a fresh import -- or a new friend --
    reads as "+1001%" when the truth is "there was nothing to compare against".
    """
    totals = [b["total"] for b in buckets]
    peak = max(range(len(buckets)), key=lambda i: totals[i]) if buckets else 0
    recent = sum(totals[-4:]) / 4 if buckets else 0.0
    prior = sum(totals[-17:-4]) / 13 if len(buckets) >= 17 else 0.0
    change = round((recent / prior - 1) * 100, 0) if prior > 0.5 else None
    if change is not None and first is not None and len(buckets) >= 17:
        p_start, p_end = buckets[-17]["start"], buckets[-4]["start"]
        covered = (p_end - max(first, p_start)).total_seconds() / (p_end - p_start).total_seconds()
        if covered < 0.5:
            change = None
    return {
        "peak_index": peak,
        "peak_value": totals[peak] if totals else 0,
        "recent_avg": round(recent, 1),
        "prior_avg": round(prior, 1),
        "change": change,
    }


def contact_series(corpus: Corpus, contact_id: str, now: datetime,
                   weeks: int = DEFAULT_WEEKS) -> dict:
    thread = corpus.direct_thread(contact_id)
    msgs = corpus.messages_in(thread.id) if thread else []
    buckets = bucket_series(msgs, now, weeks)
    # Weeks before the first message are "you had not met yet", which is a
    # different thing from silence and should not be drawn as a flat zero.
    first = msgs[0].ts if msgs else None
    return {"buckets": buckets, "summary": series_summary(buckets, first),
            "first_message": first, "weeks": weeks}


def overall_series(corpus: Corpus, now: datetime,
                   weeks: int = DEFAULT_WEEKS) -> dict:
    buckets = bucket_series(corpus.messages, now, weeks)
    first = corpus.messages[0].ts if corpus.messages else None
    return {"buckets": buckets, "summary": series_summary(buckets, first),
            "first_message": corpus.messages[0].ts if corpus.messages else None,
            "weeks": weeks}


def sparkline_points(corpus: Corpus, contact_id: str, now: datetime,
                     weeks: int = 20) -> list[int]:
    thread = corpus.direct_thread(contact_id)
    msgs = corpus.messages_in(thread.id) if thread else []
    return [b["total"] for b in bucket_series(msgs, now, weeks)]
