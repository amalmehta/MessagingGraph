"""Real conversations vs automated and marketing threads.

The import already drops the unambiguous machine senders (short codes, business
chats, numbers you never wrote to). What survives and still isn't a relationship:
a saved "Sunrise Dental" you've confirmed appointments with, a marketing number
you replied STOP to a few times, delivery and account alerts from ordinary
10-digit numbers.

Every verdict carries its reasons, so a wrong call is visible and reversible in
the UI. The bias is deliberately toward keeping people: real back-and-forth pulls
a thread toward "conversation", and anyone you've written 25+ real messages to is
never filtered automatically.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from ..models import Corpus, Thread
from .phrasing import count

MARKETING: list[tuple[re.Pattern[str], str]] = [(re.compile(p, re.I), label) for p, label in [
    (r"\breply\s+stop\b|\btext\s+stop\b|\bstop\s+to\s+(?:end|opt|quit|cancel|unsub\w*)\b|\bstop2end\b|"
     r"\bunsubscribe\b|\bopt[- ]?out\b", "opt-out instructions"),
    (r"\bmsg\s*&\s*data\s+rates\b|\bmsg\s+freq\w*\b|\bdata rates may apply\b", "carrier disclaimers"),
    (r"\b\d{1,2}%\s*off\b|\bpromo(?:tion)?\s*code\b|\buse code\b|\bcoupon\b|\bflash sale\b|"
     r"\blimited[- ]time\b|\bshop now\b|\bbuy now\b|\bfree shipping\b|\bexclusive offer\b|\bdeal ends\b",
     "promotional offers"),
    (r"\byour (?:verification|security|login|one[- ]time) code\b|\bcode is:?\s*\d{4,8}\b|"
     r"\bdo not share this code\b", "verification codes"),
    (r"\byour order\b|\border\s*#?\s*\d{3,}|\bhas shipped\b|\bout for delivery\b|\btracking number\b|"
     r"\bwas delivered\b", "order and delivery notices"),
    (r"\bappointment (?:reminder|is scheduled|is confirmed)\b|\breply\s+(?:c|y|yes|1)\s+to\s+confirm\b|"
     r"\bto confirm,? reply\b", "appointment reminders"),
    (r"\bdo not reply\b|\bno[- ]reply\b|\bthis is an automated\b|\bautomated message\b", "automated notices"),
    (r"\bdonate\b|\bchip in\b|\bpaid for by\b|\bvote (?:for|early)\b", "campaign or fundraising asks"),
    (r"\byou(?:'ve| have) won\b|\bclaim your (?:prize|reward|gift)\b|\bcongratulations!? you(?:'ve| have)\b",
     "prize-style messages"),
    (r"\bpayment (?:is )?(?:due|received|failed)\b|\baccount ending in\b|\bfraud alert\b", "account alerts"),
]]
URL = re.compile(r"https?://\S+|\b(?:bit\.ly|t\.co|tinyurl\.com|goo\.gl|ow\.ly|rb\.gy|lnkd\.in)/\S+", re.I)
COMMAND = re.compile(r"^\s*(?:stop\w*|unsubscribe|cancel|end|quit|yes|y|no|n|c|confirm|help|info|"
                     r"start|[0-9])\s*[.!]?\s*$", re.I)
BUSINESS = re.compile(
    r"\b(?:bank|credit union|pharmacy|clinic|dental|dentist|medical|hospital|insurance|delivery|"
    r"support|rewards|alerts?|notifications?|llc|inc|corp|store|salon|spa|motors|realty|apartments|"
    r"leasing|services|noreply|no-reply)\b", re.I)
NEVER_FILTER_AFTER = 25


@dataclass
class ThreadVerdict:
    thread_id: str
    name: str
    automated: bool
    score: float
    reasons: list[str] = field(default_factory=list)
    their_messages: int = 0
    your_messages: int = 0
    decided_by: str = "auto"        # "you" once you've restored or hidden it yourself


def _norm(text: str) -> str:
    text = URL.sub("<link>", text.lower())
    return re.sub(r"\s+", " ", re.sub(r"\d+", "#", text)).strip()


def classify_thread(corpus: Corpus, thread: Thread) -> ThreadVerdict:
    msgs = corpus.messages_in(thread.id)
    theirs = [m for m in msgs if not m.is_from_me and m.text]
    mine = [m for m in msgs if m.is_from_me and m.text]
    real_mine = [m for m in mine if not COMMAND.match(m.text)]
    commands = len(mine) - len(real_mine)
    verdict = ThreadVerdict(thread.id, thread.name, False, 0.0,
                            their_messages=len(theirs), your_messages=len(mine))
    if not theirs:
        return verdict

    n = len(theirs)
    labels: Counter = Counter()
    promo_msgs = 0
    for m in theirs:
        hits = {label for pat, label in MARKETING if pat.search(m.text)}
        labels.update(hits)
        promo_msgs += bool(hits)
    promo = promo_msgs / n
    links = sum(bool(URL.search(m.text)) for m in theirs) / n
    counts = Counter(_norm(m.text) for m in theirs)
    template = sum(c for c in counts.values() if c > 1) / n if n >= 3 else 0.0
    people = [corpus.contacts[c] for c in thread.participant_ids if c in corpus.contacts]
    business = any("org" in c.tags or BUSINESS.search(c.name) for c in people)

    score = (0.45 * min(1.0, promo / 0.5) + 0.2 * min(1.0, links / 0.7)
             + 0.2 * min(1.0, template / 0.6) + (0.1 if business else 0.0)
             + (0.15 if commands and len(real_mine) <= 2 else 0.0))
    if len(real_mine) >= 10:
        score -= 0.35
    elif len(real_mine) >= 4:
        score -= 0.15
    score = max(0.0, min(1.0, score))
    if len(real_mine) >= NEVER_FILTER_AFTER:
        score = min(score, 0.49)

    reasons = [f"{c} of {count(n, 'message')} from them contain {label}"
               for label, c in labels.most_common(2)]
    if links >= 0.5:
        reasons.append(f"{round(100 * links)}% of their messages are links")
    if template >= 0.5:
        reasons.append("the same message template keeps repeating")
    if commands and len(real_mine) <= 2:
        reasons.append("your only replies were commands like STOP or YES")
    if business:
        reasons.append("saved under a business name")
    if len(real_mine) >= 4:
        reasons.append(f"you've written {len(real_mine)} real messages here")

    verdict.score = round(score, 2)
    verdict.automated = score >= 0.5
    verdict.reasons = reasons
    return verdict


def classify(corpus: Corpus) -> dict[str, ThreadVerdict]:
    return {t.id: classify_thread(corpus, t) for t in corpus.threads.values()}
