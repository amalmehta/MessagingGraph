"""Relationship profiles.

Every number here is computed locally and is traceable: the summary is a weighted
sum of `Signal` objects, and the UI renders those components rather than a score
from nowhere.

A note on what this module deliberately does *not* do. It does not grade people.
Message statistics can tell you that someone has been slower to reply this quarter;
they cannot tell you that person is caring for a sick parent, changing jobs, or
having the worst year of their life -- and any of those explains a cooling thread
better than the thread does. So every signal carries a `benign` reading alongside
its `note`, states are descriptive rather than evaluative, and the strongest thing
the app says about anyone is that a connection is running *unevenly right now*.

`self_editing` is the one measure pointed at the user rather than the other person,
and even that is offered as something to notice, not a fault. Writing more
carefully to a colleague, or to someone you have known three weeks, is ordinary
good manners rather than a warning sign.
"""
from __future__ import annotations

import math
import statistics
from collections import defaultdict
from datetime import datetime, timedelta

from ..models import Corpus, Message, RelationshipProfile, Signal, SELF_ID
from ..names import short_name
from . import lexicon

CONVO_GAP = timedelta(hours=6)      # silence long enough to count as a new conversation
REPLY_CAP = 48 * 3600               # ignore "replies" slower than this as latency outliers
RECENT_DAYS = 30
PRIOR_DAYS = 120


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _clamp(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _pct(x: float) -> float:
    return round(max(0.0, min(100.0, x)), 1)


def _median(xs: list[float], default: float = 0.0) -> float:
    return statistics.median(xs) if xs else default


def _conversations(msgs: list[Message]) -> list[list[Message]]:
    convos: list[list[Message]] = []
    for m in msgs:
        if convos and m.ts - convos[-1][-1].ts <= CONVO_GAP:
            convos[-1].append(m)
        else:
            convos.append([m])
    return convos


LATE_REPLY_GAP = timedelta(hours=12)


def _initiations(msgs: list[Message]) -> tuple[int, int]:
    """Count who actually reached out first, not who happened to answer late.

    A naive "first message after six hours of silence" miscounts slow repliers:
    their delayed answer lands on the far side of the gap and reads as them
    starting a conversation. So a message only counts as an initiation if it
    follows the person's own last message, or if the silence was long enough
    (12h) that it is a fresh reach-out rather than a late reply.
    """
    mine = total = 0
    for prev, cur in zip([None, *msgs], msgs):
        if prev is None:
            fresh = True
        else:
            gap = cur.ts - prev.ts
            if gap <= CONVO_GAP:
                continue
            fresh = gap > LATE_REPLY_GAP or cur.is_from_me == prev.is_from_me
        if not fresh:
            continue
        total += 1
        mine += cur.is_from_me
    return mine, total


def _reply_latencies(msgs: list[Message]) -> tuple[list[float], list[float]]:
    """Seconds-to-reply, split into mine and theirs, within a conversation."""
    mine: list[float] = []
    theirs: list[float] = []
    for a, b in zip(msgs, msgs[1:]):
        if a.is_from_me == b.is_from_me:
            continue
        gap = (b.ts - a.ts).total_seconds()
        if gap <= 0 or gap > REPLY_CAP:
            continue
        (mine if b.is_from_me else theirs).append(gap)
    return mine, theirs


def _density(msgs: list[Message], fn) -> float:
    # Attachment-only messages have no text. They count toward volume and reply
    # timing, but letting them into a per-message language density would dilute
    # it for anyone who sends a lot of photos.
    texted = [m for m in msgs if m.text]
    if not texted:
        return 0.0
    return sum(fn(m.text) for m in texted) / len(texted)


def _rate_per_week(msgs: list[Message], since: datetime, until: datetime) -> float:
    days = max(1.0, (until - since).total_seconds() / 86400)
    n = sum(1 for m in msgs if since <= m.ts <= until)
    return n / days * 7


# ---------------------------------------------------------------------------
# baseline
# ---------------------------------------------------------------------------

class Baseline:
    """How I write in general, so per-contact numbers can be read as deviations."""

    def __init__(self, corpus: Corpus) -> None:
        mine = [m for m in corpus.messages if m.is_from_me]
        self.n = len(mine)
        self.hedge = _density(mine, lexicon.hedge_score)
        self.affection = _density(mine, lexicon.affection_score)
        self.length = _median([len(m.text) for m in mine if m.text], 40.0)
        latencies: list[float] = []
        for t in corpus.threads:
            lm, _ = _reply_latencies(corpus.messages_in(t))
            latencies += lm
        self.reply_s = _median(latencies, 600.0)


# ---------------------------------------------------------------------------
# profile
# ---------------------------------------------------------------------------

def profile_contact(corpus: Corpus, contact_id: str, base: Baseline,
                    now: datetime, open_plans: int = 0,
                    follow_through: float | None = None) -> RelationshipProfile | None:
    contact = corpus.contacts.get(contact_id)
    if contact is None:
        return None

    direct = corpus.direct_thread(contact_id)
    dm = corpus.messages_in(direct.id) if direct else []
    groups = [t for t in corpus.threads_with(contact_id) if t.is_group]
    group_msgs = [m for t in groups for m in corpus.messages_in(t.id)]

    if not dm and not group_msgs:
        return None

    if not dm:
        # Someone you only know through group chats. There's no one-on-one pattern to
        # read, and calling them "quiet" would describe a conversation that never
        # existed. They still appear in groups, group plans and connections.
        theirs_in_groups = [m for m in group_msgs if m.sender_id == contact_id]
        where = ", ".join(t.name for t in groups[:2]) + (
            f" and {len(groups) - 2} more" if len(groups) > 2 else "")
        return RelationshipProfile(
            contact_id=contact_id, name=contact.name, msgs_per_week=0.0, msgs_per_week_recent=0.0,
            trend="none", warmth=0.0, importance=_pct(min(1.0, len(theirs_in_groups) / 300) * 35),
            reciprocity=0.5, initiation_balance=0.5, my_median_reply_s=0.0, their_median_reply_s=0.0,
            self_editing=33.0, depth=0.0, balance=0.0, state="group",
            headline=(f"You know {short_name(contact.name)} through {where} rather than one-on-one, "
                      f"so there's no one-on-one pattern to describe."),
            first_contact=group_msgs[0].ts, last_contact=group_msgs[-1].ts,
            total_messages=0, open_plans=open_plans)

    mine = [m for m in dm if m.is_from_me]
    theirs = [m for m in dm if not m.is_from_me]
    total = len(dm)

    first_ts = dm[0].ts if dm else group_msgs[0].ts
    last_ts = dm[-1].ts if dm else group_msgs[-1].ts

    # -- volume and trend ------------------------------------------------
    per_week = _rate_per_week(dm, first_ts, now) if dm else 0.0
    recent = _rate_per_week(dm, now - timedelta(days=RECENT_DAYS), now)
    prior = _rate_per_week(dm, now - timedelta(days=PRIOR_DAYS),
                           now - timedelta(days=RECENT_DAYS))
    days_silent = (now - last_ts).total_seconds() / 86400

    # Compare counts rather than rates, with a pseudo-count so a quiet thread
    # cannot be called "rising" off three extra messages. A 1.6-msg/week thread
    # swinging 60% is noise; the same swing on a daily thread is a real change.
    recent_n = sum(1 for m in dm if m.ts >= now - timedelta(days=RECENT_DAYS))
    expected_n = prior / 7 * RECENT_DAYS
    K = 8.0
    ratio = (recent_n + K) / (expected_n + K)

    if days_silent > 45 or recent < 0.15:
        trend = "dormant"
    elif ratio > 1.35:
        trend = "rising"
    elif ratio < 0.6:
        trend = "fading"
    else:
        trend = "steady"

    # -- balance ---------------------------------------------------------
    reciprocity = len(theirs) / total if total else 0.5
    convos = _conversations(dm)
    starts_mine, starts_total = _initiations(dm)
    initiation = starts_mine / starts_total if starts_total else 0.5

    my_lat, their_lat = _reply_latencies(dm)
    my_med = _median(my_lat, base.reply_s)
    their_med = _median(their_lat, base.reply_s)

    # -- tone ------------------------------------------------------------
    their_affection = _density(theirs, lexicon.affection_score)
    my_affection = _density(mine, lexicon.affection_score)
    reacts_received = sum(1 for m in mine if m.reactions) / max(1, len(mine))
    their_care_q = sum(1 for m in theirs if lexicon.is_care_question(m.text)) / max(1, len(theirs))
    my_care_q = sum(1 for m in mine if lexicon.is_care_question(m.text)) / max(1, len(mine))

    warmth_received = _pct(100 * (
        0.42 * min(1.0, their_affection / 1.1)
        + 0.24 * min(1.0, reacts_received / 0.14)
        + 0.22 * min(1.0, their_care_q / 0.22)
        + 0.12 * min(1.0, len(theirs) / max(1, total) / 0.5)
    ))
    warmth_given = _pct(100 * (
        0.55 * min(1.0, my_affection / 1.1) + 0.45 * min(1.0, my_care_q / 0.22)
    ))
    warmth = _pct(0.6 * warmth_received + 0.4 * warmth_given)

    # -- depth -----------------------------------------------------------
    # Depth is carried mostly by message length. Late-night share is measured on
    # conversation *openings* rather than every message, because a slow-replying
    # thread drifts past midnight on its own and that says nothing about intimacy.
    lengths = [len(m.text) for m in dm if m.text]
    long_share = sum(1 for n in lengths if n > 120) / max(1, len(lengths))
    opens = [c[0] for c in convos]
    late_share = sum(1 for m in opens if m.ts.hour >= 22 or m.ts.hour < 4) / max(1, len(opens))
    med_len = _median(lengths, base.length)
    depth = _pct(100 * (0.6 * min(1.0, long_share / 0.035)
                        + 0.22 * min(1.0, late_share / 0.2)
                        + 0.18 * min(1.0, med_len / (base.length * 1.5))))

    # -- self-editing, relative to my own baseline -----------------------
    my_hedge = _density(mine, lexicon.hedge_score)
    hedge_ratio = my_hedge / base.hedge if base.hedge > 0 else 1.0
    self_editing = _pct(33 * hedge_ratio)          # 33 == my normal, 100 == triple it

    # -- importance ------------------------------------------------------
    recency = math.exp(-days_silent / 21)
    volume_share = recent / 12
    importance = _pct(100 * (
        0.34 * min(1.0, volume_share)
        + 0.24 * recency
        + 0.16 * min(1.0, len(groups) / 2)
        + 0.14 * (depth / 100)
        + 0.12 * min(1.0, open_plans / 2)
    ))

    # -- confidence ------------------------------------------------------
    # A dozen messages cannot support a claim about a friendship. Rather than
    # hide thin threads, we measure them and label the reading as provisional.
    confidence = round(min(1.0, len(mine) / 60) ** 0.6 * min(1.0, len(convos) / 12) ** 0.4, 2)

    # -- signals ---------------------------------------------------------
    signals: list[Signal] = []

    bal = 1 - abs(reciprocity - 0.5) / 0.5
    signals.append(Signal(
        "reciprocity", "Who's doing the talking", reciprocity,
        f"{round(100 * (1 - reciprocity))}% you / {round(100 * reciprocity)}% them",
        _clamp((bal - 0.82) / 0.18), 0.18,
        "How the messages split between you.",
        "Most friendships take turns carrying the conversation, and the split moves "
        "around depending on whose life is busier. A lopsided month is usually just "
        "a month."))

    signals.append(Signal(
        "initiation", "Who reaches out first", initiation,
        f"you start {round(100 * initiation)}% of conversations",
        _clamp((0.55 - initiation) / 0.3), 0.16,
        "Who tends to open a conversation after a quiet stretch.",
        "Plenty of people who love you will almost never text first -- it's a habit, "
        "not a verdict on the friendship. Worth knowing if you'd like it more even, "
        "and worth saying out loud rather than keeping score."))

    lat_ratio = their_med / max(60.0, my_med)
    signals.append(Signal(
        "latency", "Reply speed, them and you", lat_ratio,
        f"they reply in {_human(their_med)}, you in {_human(my_med)}",
        _clamp((2.2 - lat_ratio) / 2.2), 0.13,
        "Typical time to answer, in each direction.",
        "Reply speed mostly measures someone's relationship with their phone. Slow "
        "repliers are often the same with everyone, and a busy season can stretch "
        "this for months without anything being wrong."))

    signals.append(Signal(
        "warmth_received", "Warmth coming back to you", warmth_received,
        f"{warmth_received:.0f}/100",
        _clamp((warmth_received - 38) / 42), 0.2,
        "Affection, laughter, reactions, and them asking how you are.",
        "This only counts warmth that shows up in text. Someone who is undemonstrative "
        "by message may be the first to appear when it actually matters, and that "
        "never lands in this number."))

    edit_dev = hedge_ratio - 1.0
    signals.append(Signal(
        "self_editing", "How much you edit yourself", self_editing,
        ("about your usual" if abs(edit_dev) < 0.25 else
         f"{abs(edit_dev) * 100:.0f}% {'more' if edit_dev > 0 else 'less'} than your usual"),
        _clamp(-edit_dev / 1.2), 0.19,
        "How much you hedge, soften, and apologise here, against how you write to "
        "everyone else.",
        "This one is about you, not them. Extra care is completely normal with "
        "colleagues, new friends, and anyone you're still learning. It's only worth "
        "sitting with if the number surprises you."))

    if follow_through is not None:
        signals.append(Signal(
            "follow_through", "Do plans with them happen", follow_through,
            f"{follow_through * 100:.0f}% of plans actually land",
            _clamp((follow_through - 0.45) / 0.4), 0.12,
            "How often plans you both wanted got an actual date.",
            "Plans usually stall on scheduling friction, not reluctance -- two full "
            "calendars and nobody wanting to be the one who names a day. They tend to "
            "revive the moment someone suggests one."))

    signals.append(Signal(
        "curiosity", "Do they ask how you are", their_care_q,
        f"{their_care_q * 100:.0f}% of their messages ask about you",
        _clamp((their_care_q - 0.07) / 0.16), 0.14,
        "How often their messages turn the attention to you.",
        "Asking is one way of showing care and not everyone's way. Some people show "
        "up by remembering the details, or by turning up when you need them."))

    raw = 100 * sum(s.contribution for s in signals) / max(0.001, sum(s.weight for s in signals))
    # A thread that is even, prompt, and entirely practical is not "close" -- it is
    # working, which is a fine thing for a friendship to be. Positive scores are
    # scaled by how much warmth is present so the two don't get confused.
    if raw > 0:
        raw *= 0.35 + 0.65 * min(1.0, warmth_received / 45)
    raw *= 0.45 + 0.55 * confidence          # thin data pulls the reading to the middle
    balance = round(raw, 1)

    # These are descriptions of how a connection is running, not grades. Nothing
    # here says a person is good or bad to have in your life -- only what the
    # pattern looks like at the moment, which can change next week.
    if confidence < 0.45:
        state = "new"
    elif trend == "dormant":
        state = "quiet"
    elif balance <= -12:
        state = "uneven"
    elif balance >= 35 and warmth_received >= 55:
        state = "close"
    elif balance >= 16 and warmth_received < 35:
        state = "practical"
    else:
        state = "steady"

    return RelationshipProfile(
        contact_id=contact_id, name=contact.name,
        msgs_per_week=round(per_week, 1), msgs_per_week_recent=round(recent, 1),
        trend=trend, warmth=warmth, importance=importance,
        reciprocity=round(reciprocity, 3), initiation_balance=round(initiation, 3),
        my_median_reply_s=round(my_med), their_median_reply_s=round(their_med),
        self_editing=self_editing, depth=depth, balance=balance, state=state,
        signals=signals,
        headline=_headline(contact.name, state, signals, trend, confidence,
                           per_week, recent),
        first_contact=first_ts, last_contact=last_ts,
        total_messages=total, open_plans=open_plans,
        confidence=confidence,
        follow_through=None if follow_through is None else round(follow_through, 3),
    )


def _human(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:.0f}s"
    if seconds < 5400:
        return f"{seconds / 60:.0f}m"
    if seconds < 172800:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 86400:.1f}d"


def _headline(name: str, state: str, signals: list[Signal], trend: str,
              confidence: float, per_week: float, recent: float) -> str:
    """One sentence describing the connection, written to be read by a person who
    cares about the person being described.

    The rule: say what is happening, offer the ordinary explanation, and leave the
    conclusion to the reader. Never tell someone a friendship is bad for them.
    """
    first = short_name(name)
    ranked = sorted(signals, key=lambda s: s.contribution)
    worst, best = ranked[0], ranked[-1]

    if state == "new":
        return (f"There isn't much here yet \u2014 not enough back-and-forth with {first} "
                f"to say anything meaningful, which is exactly what a new thread "
                f"looks like.")
    if state == "quiet":
        return (f"You and {first} used to talk about {per_week:.0f} times a week and "
                f"it's gone quiet. Threads drift for all sorts of reasons that have "
                f"nothing to do with how either of you feels about the other.")
    if state == "uneven":
        if worst.key == "self_editing":
            # The driving measurement is about how the user writes, so the sentence
            # has to be about them. Pinning it on the friend would be inaccurate.
            return (f"You write to {first} more carefully than you write to most people "
                    f"\u2014 {worst.display}. That is often just context rather than a "
                    f"problem; worth a look only if it surprises you.")
        return (f"You've been carrying more of this one lately \u2014 "
                f"{worst.label.lower()}: {worst.display}. That may simply be the season "
                f"{first} is in; it's worth knowing either way.")
    if state == "practical":
        return (f"Mostly practical, which suits plenty of good friendships. If you "
                f"wanted more from it, one message that isn't about logistics is "
                f"usually all it takes.")
    if state == "close":
        # Prefer a warmth-flavoured signal for the highlight; "50% you / 50% them"
        # is true but a strange thing to lead with about a close friendship.
        warm = [x for x in signals
                if x.key in ("warmth_received", "curiosity") and x.contribution > 0]
        lead = max(warm, key=lambda x: x.contribution) if warm else best
        return (f"This one's in good shape \u2014 {lead.label.lower()}: {lead.display}"
                + (" \u2014 and it's growing." if trend == "rising" else "."))
    return (f"Comfortable and holding. {best.label} is working well ({best.display}); "
            f"{worst.label.lower()} is the quieter side of it ({worst.display}).")


def profile_all(corpus: Corpus, now: datetime | None = None,
                open_plans: dict[str, int] | None = None,
                follow_through: dict[str, float] | None = None) -> list[RelationshipProfile]:
    now = now or datetime.now()
    base = Baseline(corpus)
    open_plans = open_plans or {}
    follow_through = follow_through or {}
    out = []
    for cid in corpus.contacts:
        p = profile_contact(corpus, cid, base, now, open_plans.get(cid, 0),
                            follow_through.get(cid))
        if p:
            out.append(p)
    out.sort(key=lambda p: p.importance, reverse=True)
    return out
