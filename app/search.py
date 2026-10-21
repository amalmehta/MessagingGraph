"""Asking the app a question in the words you'd use out loud.

"what dates are we going to coachella" is not a query, it is a sentence, and
almost all of it is scaffolding. The words that matter are the ones that are
rare in your own history -- coachella appears in one thread and nowhere else,
while "going" is in thousands of messages and tells you nothing about where to
look. So terms are weighted by how rare they are here, which needs no model and
no network, and the question words are dropped before any of that.

A question about *when* is answered by a plan, because plans are the only thing
in this app that carry a date. A question about a person is answered by their
profile. Everything else falls back to the messages the words came from.
"""
from __future__ import annotations

import math
import re
import unicodedata
from functools import lru_cache
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from .models import SELF_ID, Corpus, Plan, RelationshipProfile

# Scaffolding. Dropped from the query, never from what is searched.
STOP = {
    "a", "an", "the", "and", "or", "but", "if", "of", "to", "in", "on", "at", "for",
    "with", "about", "from", "by", "is", "are", "was", "were", "be", "been", "am",
    "do", "does", "did", "have", "has", "had", "i", "me", "my", "we", "us", "our",
    "you", "your", "he", "she", "it", "they", "them", "their", "this", "that",
    "these", "those", "what", "when", "where", "who", "whom", "which", "why", "how",
    "going", "go", "get", "got", "any", "some", "all", "s", "t", "re", "m", "ll",
    "can", "will", "would", "should", "could", "there", "here", "up", "out", "so",
    "again", "still", "just", "like", "said", "say", "tell", "know", "think",
    "talk", "talked", "talking", "going", "went", "want", "need", "make", "made",
}
# A question that wants a date rather than a conversation.
WHEN = re.compile(r"\b(when|what\s+(?:date|dates|day|days|time)|which\s+(?:day|days|date|dates)|"
                  r"how\s+soon|what\s+time)\b", re.I)
WHO = re.compile(r"\b(who|whose|which\s+(?:person|people|friend))\b", re.I)
# Letters in any script. This was [a-z0-9'], so "где встреча" and "東京" had no
# words in them at all and found nothing.
WORD = re.compile(r"[\w']+")
# Scripts written without spaces between words, where a word is found inside a
# run of text rather than between boundaries.
UNSPACED = re.compile(r"[\u0e00-\u0e7f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
# Enough of a date to be worth showing when the question asked for one.
DATEISH = re.compile(
    r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2}(?:st|nd|rd|th)?\b"
    r"|\b\d{1,2}(?:st|nd|rd|th)\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b"
    r"|\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b"
    r"|\b(?:mon|tues?|wed(?:nes)?|thur?s?|fri|sat(?:ur)?|sun)(?:day)?\b"
    r"|\b(?:this|next|last)\s+(?:week|weekend|month|year)\b|\bweekend\b", re.I)
MAX_PER_KIND = 6


def fold(text: str) -> str:
    """Lowercase, with accents taken off, so "Café" in a message and "cafe" in
    a question are the same word. Both sides go through this: folding only the
    question turned "café" into "cafe" and then looked for it in "café"."""
    flat = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in flat if not unicodedata.combining(c))


# Long enough for any real word, short enough that a paste of a whole message
# does not become one enormous term that matches nothing and caches a regex.
MAX_TERM = 40


def terms(q: str) -> list[str]:
    """The words worth searching for, in the order they were typed.

    Each one once: "ski ski ski trip" was four terms of which two were
    distinct, so coverage counted the repeat three times and the rare word
    was outvoted by its own echo.
    """
    words = [w.strip("'")[:MAX_TERM] for w in WORD.findall(fold(q))]
    kept = [w for w in words if w and w not in STOP and len(w) > 1]
    out = kept or [w for w in words if w]         # a query of nothing but stopwords
    return list(dict.fromkeys(out))


@dataclass
class Hit:
    kind: str                     # plan | person | message | group
    score: float
    title: str
    detail: str = ""
    when: str = ""                # the answer, where there is one
    target_id: str = ""           # what to open
    who: str = ""
    ts: datetime | None = None


@dataclass
class Answer:
    query: str
    terms: list[str] = field(default_factory=list)
    asked_when: bool = False
    asked_who: bool = False
    hits: list[Hit] = field(default_factory=list)
    searched: int = 0
    no_date: bool = False
    off_subject: str = ""     # the word nothing matched, when that is the story

    def to_dict(self) -> dict:
        return {"query": self.query, "terms": self.terms,
                "asked_when": self.asked_when, "asked_who": self.asked_who,
                "searched": self.searched, "no_date": self.no_date,
                "off_subject": self.off_subject,
                "hits": [{"kind": h.kind, "score": round(h.score, 2), "title": h.title,
                          "detail": h.detail, "when": h.when, "target_id": h.target_id,
                          "who": h.who, "ts": h.ts.isoformat() if h.ts else None}
                         for h in self.hits]}


def _weights(corpus: Corpus, words: list[str]) -> dict[str, float]:
    """How much each word narrows things down, measured against this history.

    A word in one message out of fourteen thousand is worth a great deal more
    than one in half of them, and that is the whole reason "coachella" beats
    "going" without anyone having to say so.
    """
    counts = Counter()
    for m in corpus.messages:
        if not m.text:
            continue
        low = fold(m.text)
        seen = set(WORD.findall(low))
        for w in words:
            if w in seen or (UNSPACED.search(w) and w in low):
                counts[w] += 1
    total = max(1, len(corpus.messages))
    return {w: math.log(1 + total / (1 + counts[w])) for w in words}


@lru_cache(maxsize=4096)
def _word_re(word: str):
    if UNSPACED.search(word):
        return re.compile(re.escape(word))
    return re.compile(rf"(?<![\w]){re.escape(word)}(?![\w])")


def _word_in(word: str, low: str) -> bool:
    return bool(_word_re(word).search(low))


def _matches(text: str, words: list[str], weight: dict[str, float],
             pivot: str = "") -> float:
    """How well a piece of text answers the question.

    Weighted by rarity, then by how much of the question it covers, and then by
    whether it contains the one word the question is really about. Without that
    last part, "what dates are we going to coachella" answered itself with
    "Tentative dates June 1 - June 8" from an unrelated thread: it matched the
    common word and missed the rare one, and a date bonus carried it to the top.
    A miss on the pivot is not a weaker answer, it is a different subject.
    """
    if not text:
        return 0.0
    low = fold(text)
    hit = [w for w in words if _word_in(w, low)]
    if not hit:
        return 0.0
    coverage = len(hit) / len(words)
    score = sum(weight.get(w, 1.0) for w in hit) * (0.4 + 0.6 * coverage) * (1 + coverage)
    if pivot and not _word_in(pivot, low):
        score *= 0.12
    return score


def search(corpus: Corpus, plans: list[Plan], profiles: list[RelationshipProfile],
           query: str, now: datetime | None = None, limit: int = 18,
           interests: dict[str, dict[str, int]] | None = None) -> Answer:
    now = now or datetime.now()
    words = terms(query)
    out = Answer(query=query, terms=words,
                 asked_when=bool(WHEN.search(query)), asked_who=bool(WHO.search(query)),
                 searched=len(corpus.messages))
    if not words:
        return out
    weight = _weights(corpus, words)
    # The rarest word carries the subject. With one word there is nothing to
    # weigh it against, so nothing is required.
    pivot = max(words, key=lambda w: weight.get(w, 0.0)) if len(words) > 1 else ""
    names = {p.contact_id: p.name for p in profiles}
    hits: list[Hit] = []

    # Plans first: they are the only thing here that knows a date, so a "when"
    # question is really a question about them.
    for p in plans:
        s = (_matches(p.title, words, weight, pivot) * 2.2
             + _matches(p.excerpt, words, weight, pivot))
        s += _matches(p.thread_name, words, weight, pivot) * 0.6
        if s <= 0:
            continue
        if out.asked_when and (p.when_date or p.when_text):
            s *= 2.0
        if p.state in ("confirmed", "scheduled"):
            s *= 1.3
        elif p.state in ("done", "dropped"):
            s *= 0.6
        if out.asked_who:
            s *= 0.45         # you asked who, and a plan is a what
        when = p.when_text or (p.when_date.strftime("%A %-d %B") if p.when_date else "")
        hits.append(Hit("plan", s, p.title, p.excerpt[:160], when, p.id,
                        p.thread_name, p.last_activity))

    # "who do i talk to about cycling" is a question about people, and the app
    # already works out what each person is the one you discuss.
    topics = interests or {}
    for prof in profiles:
        s = _matches(prof.name, words, weight, pivot) * 2.5
        theirs = topics.get(prof.contact_id) or {}
        shared = [t for t in theirs if any(w in fold(t) for w in words)]
        detail = prof.headline or ""
        if shared:
            s += 6.0 * len(shared)
            detail = f"you two talk about {', '.join(sorted(shared))}"
        if s <= 0:
            continue
        if out.asked_who:
            s *= 2.2
        hits.append(Hit("person", s, prof.name, detail, "",
                        prof.contact_id, prof.name, prof.last_contact))

    for t in corpus.threads.values():
        if not t.is_group:
            continue
        s = _matches(t.name, words, weight, pivot) * 1.8
        if s > 0:
            hits.append(Hit("group", s, t.name,
                            f"{len(t.participant_ids)} people", "", t.id, t.name))

    # And the messages the words actually came from, newest first among equals.
    oldest = min((m.ts for m in corpus.messages), default=now)
    span = max(1.0, (now - oldest).total_seconds())
    for m in corpus.messages:
        s = _matches(m.text, words, weight, pivot)
        if s <= 0:
            continue
        # Recent messages are usually the ones being asked about.
        s *= 1.0 + 0.35 * ((m.ts - oldest).total_seconds() / span)
        # If a date was asked for, a message that names one is the answer and
        # the rest is context.
        said_when = ""
        if out.asked_when:
            found = DATEISH.search(m.text)
            if found and (not pivot or _word_in(pivot, fold(m.text))):
                said_when = found.group(0)
                s *= 2.4
        thread = corpus.threads.get(m.thread_id)
        who = "You" if m.is_from_me else names.get(m.sender_id, thread.name if thread else "")
        if out.asked_who:
            s *= 0.5          # you asked who, not what was said
        hits.append(Hit("message", s, m.text[:200], thread.name if thread else "", said_when,
                        m.thread_id, who, m.ts))

    # If anything is actually about the subject, nothing else is an answer.
    # "when is the ski trip" was offering six Cabin Trip plans, none of which
    # mention skiing, dressed up as matches because they had the word "trip".
    if pivot:
        on_subject = [h for h in hits if _word_in(pivot, fold(f"{h.title} {h.detail} {h.who}"))]
        if on_subject:
            hits = on_subject
        else:
            out.off_subject = pivot

    hits.sort(key=lambda h: (-h.score, -(h.ts.timestamp() if h.ts else 0)))
    kept: list[Hit] = []
    per = Counter()
    for h in hits:
        if per[h.kind] >= MAX_PER_KIND:
            continue
        per[h.kind] += 1
        kept.append(h)
        if len(kept) >= limit:
            break
    out.hits = kept
    # Asked for a date and nothing that matched contains one: say so rather
    # than leaving a list of messages to be read hopefully.
    out.no_date = out.asked_when and not any(h.when for h in kept)
    return out
