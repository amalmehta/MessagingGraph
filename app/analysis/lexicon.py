"""Detection lexicons.

Written independently of the synthetic generator on purpose: the generator is
parameterized by persona *behaviour*, and these patterns are what a detector
would plausibly look for in real text. Agreement between the two is therefore a
result, not a lookup.
"""
from __future__ import annotations

import re
from functools import lru_cache

# Softening, apologizing, pre-emptively excusing yourself.
HEDGE_PATTERNS = [
    r"\bsorry\b", r"\bapologi", r"no worries if", r"no pressure", r"\bif that'?s ok",
    r"hope that'?s ok", r"just wondering", r"feel free to ignore", r"ignore me",
    r"don'?t want to (?:bother|be a pain|impose)", r"no rush", r"totally fine either way",
    r"not a big deal", r"if you'?re not busy", r"whenever you (?:get|have)",
    r"hope this isn'?t", r"is that (?:ok|cool)", r"let me know if that'?s",
    r"\bmy bad\b", r"if it'?s not too much",
]
# Low-commitment qualifiers. Weighted less than an outright apology.
QUALIFIER_PATTERNS = [
    r"\bjust\b", r"\bmaybe\b", r"\bkinda\b", r"\bsorta\b", r"\bsort of\b",
    r"\bkind of\b", r"\bi think\b", r"\bi guess\b", r"\bprobably\b", r"\bpossibly\b",
]
AFFECTION_PATTERNS = [
    r"\blove\b", r"miss you", r"\bproud\b", r"so glad", r"\bappreciate\b",
    r"thank you", r"\bthanks\b", r"you'?re the best", r"made my day", r"\bcare about\b",
    r"in your corner", r"thinking (?:about|of) you", r"\bxoxo\b", r"<3",
    r"\bcongrats\b", r"\bhugs?\b", r"you did (?:great|amazing|so well)",
]
LAUGHTER_PATTERNS = [r"\bhaha", r"\blol\b", r"\blmao\b", r"\bcrying\b", r"\bscreaming\b", r"😂", r"🤣"]
WARM_EMOJI = "❤️🥰🥺😍🫶💛💜🧡😊🙂🤗💕"

CARE_QUESTION_PATTERNS = [
    r"how are you", r"how'?s your", r"how'?d (?:it|that) go", r"are you ok",
    r"how are things", r"how'?re you", r"what'?s new with you", r"how'?ve you been",
    r"how are you feeling", r"you good\b", r"did you hear back", r"how was",
]

_H = [re.compile(p, re.I) for p in HEDGE_PATTERNS]
_Q = [re.compile(p, re.I) for p in QUALIFIER_PATTERNS]
_A = [re.compile(p, re.I) for p in AFFECTION_PATTERNS]
_L = [re.compile(p, re.I) for p in LAUGHTER_PATTERNS]
_C = [re.compile(p, re.I) for p in CARE_QUESTION_PATTERNS]


def _hits(text: str, pats) -> int:
    return sum(1 for p in pats if p.search(text))


# These three read one message and nothing else, so the answer only ever depends on
# the text. Every pass over the corpus asks about the same messages again -- profiles,
# vibes and signals each want them -- and excluding one person re-runs the lot. Keeping
# the answers means that costs about half what it did, and a second exclude costs less
# again. The key is the message text the corpus is already holding, so this adds a dict
# entry per distinct message, not a second copy of your history.
@lru_cache(maxsize=None)
def hedge_score(text: str) -> float:
    """Per-message softening load. Apologies count full, qualifiers count a third."""
    return _hits(text, _H) + 0.34 * _hits(text, _Q)


@lru_cache(maxsize=None)
def affection_score(text: str) -> float:
    score = float(_hits(text, _A)) + 0.6 * _hits(text, _L)
    score += 0.7 * sum(1 for ch in text if ch in WARM_EMOJI)
    if "!!" in text:
        score += 0.4
    return score


@lru_cache(maxsize=None)
def is_care_question(text: str) -> bool:
    return any(p.search(text) for p in _C)


def is_question(text: str) -> bool:
    return "?" in text
