"""Core data types shared by ingestion, analysis, and the API layer.

Everything downstream of ingestion speaks these types, so swapping the synthetic
source for a real iMessage source changes nothing but the loader.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Protocol


@dataclass(frozen=True)
class Contact:
    id: str
    name: str
    handle: str            # phone/email, as it would appear in a real message store
    # Notes the user has set themselves; never inferred, never sent anywhere.
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class Message:
    id: str
    thread_id: str
    sender_id: str         # contact id, or SELF_ID
    ts: datetime
    text: str
    is_from_me: bool
    # Tapbacks/reactions received on this message, e.g. ("love", "laugh").
    reactions: tuple[str, ...] = ()


@dataclass(frozen=True)
class Thread:
    id: str
    name: str
    participant_ids: tuple[str, ...]   # excludes SELF_ID
    is_group: bool


@dataclass
class Corpus:
    """One fully loaded message store."""
    contacts: dict[str, Contact]
    threads: dict[str, Thread]
    messages: list[Message]            # chronological
    source_name: str = "unknown"

    def messages_in(self, thread_id: str) -> list[Message]:
        return [m for m in self.messages if m.thread_id == thread_id]

    def threads_with(self, contact_id: str) -> list[Thread]:
        return [t for t in self.threads.values() if contact_id in t.participant_ids]

    def direct_thread(self, contact_id: str) -> Thread | None:
        for t in self.threads.values():
            if not t.is_group and t.participant_ids == (contact_id,):
                return t
        return None

    @property
    def span(self) -> tuple[datetime, datetime]:
        return self.messages[0].ts, self.messages[-1].ts


SELF_ID = "__me__"


class MessageSource(Protocol):
    """The one seam between the app and wherever messages actually live.

    A real iMessage reader, a WhatsApp export parser, and the synthetic generator
    are interchangeable as long as they satisfy this.
    """

    name: str

    def load(self) -> Corpus: ...


# ---------------------------------------------------------------------------
# Analysis result types
# ---------------------------------------------------------------------------

@dataclass
class Signal:
    """One explainable component of a relationship reading.

    `value` is the raw measurement, `score` is normalized to -1..1 where positive
    means the connection is running evenly. `weight` is its share of the summary.

    `note` says what the measurement means; `benign` gives the ordinary, innocent
    reading of the same number. Both are always shown. A statistic about texting
    cannot see a hard month, a new baby, or a job that eats someone alive, and the
    interface should never pretend otherwise.
    """
    key: str
    label: str
    value: float
    display: str
    score: float
    weight: float
    note: str = ""
    benign: str = ""
    contribution: float = 0.0

    def __post_init__(self) -> None:
        # Stored rather than computed so it survives serialization to the UI,
        # which renders the summary as the sum of these bars.
        object.__setattr__(self, "contribution", round(self.score * self.weight, 4))


@dataclass
class Vibe:
    """Tone and a short description. Descriptive only: never feeds `state` or
    `balance`, because a heavy stretch usually means trust, not trouble."""
    name: str
    line: str
    mood: float                     # -100..100, texting-aware sentiment of the thread
    mood_theirs: float
    mood_mine: float
    positive_share: float
    heavy_share: float
    mood_shift: str                 # brighter lately | steady | heavier lately | not enough to say
    mood_note: str
    dimensions: dict[str, float] = field(default_factory=dict)
    top: list[str] = field(default_factory=list)


@dataclass
class RelationshipProfile:
    contact_id: str
    name: str
    # Headline metrics
    msgs_per_week: float
    msgs_per_week_recent: float
    trend: str                      # rising | steady | fading | dormant
    warmth: float                   # 0..100
    importance: float               # 0..100, "current importance to your life"
    reciprocity: float              # 0..1, 0.5 is balanced
    initiation_balance: float       # 0..1, share of conversations I start
    my_median_reply_s: float
    their_median_reply_s: float
    self_editing: float             # 0..100, relative to my own baseline
    depth: float                    # 0..100
    # Verdict
    balance: float                  # -100..100, how evenly the connection runs
    state: str                      # close|steady|practical|uneven|quiet|new
    signals: list[Signal] = field(default_factory=list)
    headline: str = ""
    first_contact: datetime | None = None
    last_contact: datetime | None = None
    total_messages: int = 0
    open_plans: int = 0
    confidence: float = 1.0         # 0..1, how much data this read rests on
    # None when there were too few plans to judge -- which is not the same as
    # a share of 0.0, and must not be scored as though it were.
    follow_through: float | None = None
    vibe: Vibe | None = None


@dataclass
class Plan:
    id: str
    title: str
    thread_id: str
    thread_name: str
    participant_ids: list[str]
    state: str                      # floated|proposed|confirmed|scheduled|stale|done|dropped
    confidence: float               # 0..1 in the extraction itself
    first_seen: datetime
    last_activity: datetime
    when_text: str | None = None    # the time expression as written
    when_date: datetime | None = None
    evidence: list[str] = field(default_factory=list)   # message ids
    excerpt: str = ""
