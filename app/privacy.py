"""Privacy controls.

Design stance: the app is local-only and read-only by construction, and the only
way message content can leave the machine is through `NetworkGate`, which refuses
unless the user has explicitly opened consent for that specific purpose. Every
crossing is recorded in an audit log the UI can display.

Nothing here is decorative -- the LLM seam in analysis/llm.py cannot make a call
without going through this module first.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta

from .models import Corpus, Message

# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------

# A run of digits with the separators people type between them. Whether it is a
# phone number is decided by counting digits, below, not by the shape of one
# country's numbers: the pattern this replaced knew only (415) 555-0134, and let
# +91 98765 43210, +49 30 1234567 and most of +44 7700 900123 straight through.
_DIGIT_RUN = re.compile(r"(?<![\w+])(?:\+|00)?\(?\d[\d ().\-]{5,}\d(?!\w)")


def _phone(m: re.Match[str]) -> str:
    raw = m.group(0)
    digits = sum(c.isdigit() for c in raw)
    if raw.startswith(("+", "00")):
        return "[phone]" if 8 <= digits <= 15 else raw      # E.164 tops out at 15
    # Without a country code, the national lengths: 10 for North America and
    # India, 11 for a UK 07... mobile. Dates and prices are shorter than that.
    return "[phone]" if 10 <= digits <= 11 else raw


_PATTERNS: list[tuple[str, re.Pattern[str], object]] = [
    ("[email]", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]{2,}\b"), "[email]"),
    # Before the card and phone patterns, which would otherwise take its digits
    # and leave the country and bank letters behind.
    ("[iban]", re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,3})?\b"), "[iban]"),
    ("[card]", re.compile(r"\b(?:\d[ -]*?){13,16}\b"), "[card]"),
    ("[phone]", _DIGIT_RUN, _phone),
    ("[ssn]", re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[ssn]"),
    ("[url]", re.compile(r"https?://\S+"), "[url]"),
    ("[address]", re.compile(r"\b\d{1,5}\s+[A-Z][a-z]+\s+(?:St|Street|Ave|Avenue|Rd|Road|Blvd|Ln|Lane|Dr|Drive)\b"), "[address]"),
    ("[code]", re.compile(r"\b\d{6}\b"), "[code]"),  # 2FA-shaped
]


def redact(text: str) -> str:
    """Strip the obvious identifiers out of a message body."""
    for _token, pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


def redaction_hits(text: str) -> list[str]:
    """What `redact` takes out, in the order it does, so a phone number is not
    also reported as the 2FA code its last six digits look like."""
    hits = []
    for token, pattern, repl in _PATTERNS:
        out = pattern.sub(repl, text)
        if out != text:
            hits.append(token)
            text = out
    return hits


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

@dataclass
class PrivacySettings:
    #: Master switch. While True no network call is permitted for any reason.
    local_only: bool = True
    #: Purposes the user has explicitly consented to, e.g. {"draft", "tone"}.
    network_consent: set[str] = field(default_factory=set)
    #: Contacts excluded from all analysis, as if they were never in the store.
    excluded_contact_ids: set[str] = field(default_factory=set)
    #: Threads excluded from all analysis.
    excluded_thread_ids: set[str] = field(default_factory=set)
    #: Ignore anything older than this. 0 disables the cutoff.
    retention_days: int = 0
    #: Redact identifiers from any body that crosses a boundary.
    redact_on_send: bool = True
    #: Never let raw bodies leave, only derived statistics.
    stats_only: bool = False

    def to_dict(self) -> dict:
        d = asdict(self)
        d["network_consent"] = sorted(self.network_consent)
        d["excluded_contact_ids"] = sorted(self.excluded_contact_ids)
        d["excluded_thread_ids"] = sorted(self.excluded_thread_ids)
        return d


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------

def apply_settings(corpus: Corpus, s: PrivacySettings) -> Corpus:
    """Return a corpus with excluded contacts, threads, and old messages removed.

    Analysis only ever sees the filtered corpus, so an excluded contact cannot
    leak in through a group thread's statistics either.
    """
    cutoff = None
    if s.retention_days > 0:
        cutoff = datetime.now() - timedelta(days=s.retention_days)

    contacts = {k: v for k, v in corpus.contacts.items() if k not in s.excluded_contact_ids}

    threads = {}
    for tid, t in corpus.threads.items():
        if tid in s.excluded_thread_ids:
            continue
        if any(p in s.excluded_contact_ids for p in t.participant_ids):
            # A group containing an excluded person is dropped whole rather than
            # silently analyzed with a hole in it.
            continue
        threads[tid] = t

    messages = [
        m for m in corpus.messages
        if m.thread_id in threads and (cutoff is None or m.ts >= cutoff)
    ]

    return Corpus(contacts=contacts, threads=threads, messages=messages,
                  source_name=corpus.source_name)


# ---------------------------------------------------------------------------
# The network gate
# ---------------------------------------------------------------------------

class ConsentError(RuntimeError):
    """Raised when something tries to cross the boundary without permission."""


@dataclass
class AuditEntry:
    ts: datetime
    purpose: str
    allowed: bool
    reason: str
    chars: int
    redactions: list[str]
    preview: str


class NetworkGate:
    """Single chokepoint for anything leaving the machine."""

    def __init__(self, settings: PrivacySettings) -> None:
        self.settings = settings
        self.audit: list[AuditEntry] = []

    def preview(self, purpose: str, payload: str) -> dict:
        """What *would* be sent, without sending it. Feeds the confirm dialog."""
        body = redact(payload) if self.settings.redact_on_send else payload
        allowed, reason = self._check(purpose)
        return {
            "purpose": purpose,
            "would_send": body,
            "chars": len(body),
            "redactions": redaction_hits(payload),
            "allowed": allowed,
            "reason": reason,
        }

    def _check(self, purpose: str) -> tuple[bool, str]:
        if self.settings.local_only:
            return False, "local_only is on; no network calls are permitted"
        if self.settings.stats_only:
            return False, "stats_only is on; derived numbers may leave but message text may not"
        if purpose not in self.settings.network_consent:
            return False, f"no consent recorded for purpose '{purpose}'"
        return True, "consent on file"

    def authorize(self, purpose: str, payload: str) -> str:
        """Return the exact text permitted to leave, or raise.

        Callers must use the returned string rather than their own payload, so
        redaction cannot be bypassed by accident.
        """
        allowed, reason = self._check(purpose)
        body = redact(payload) if self.settings.redact_on_send else payload
        self.audit.append(AuditEntry(
            ts=datetime.now(), purpose=purpose, allowed=allowed, reason=reason,
            chars=len(body), redactions=redaction_hits(payload),
            preview=body[:280],
        ))
        if not allowed:
            raise ConsentError(reason)
        return body

    def grant(self, purpose: str) -> None:
        self.settings.local_only = False
        self.settings.network_consent.add(purpose)

    def revoke(self, purpose: str | None = None) -> None:
        if purpose is None:
            self.settings.network_consent.clear()
            self.settings.local_only = True
        else:
            self.settings.network_consent.discard(purpose)
