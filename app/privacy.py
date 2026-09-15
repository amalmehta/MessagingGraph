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

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("[email]", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]{2,}\b")),
    ("[card]", re.compile(r"\b(?:\d[ -]*?){13,16}\b")),
    ("[phone]", re.compile(r"\b(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]?\d{3}[ .-]?\d{4}\b")),
    ("[ssn]", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("[url]", re.compile(r"https?://\S+")),
    ("[address]", re.compile(r"\b\d{1,5}\s+[A-Z][a-z]+\s+(?:St|Street|Ave|Avenue|Rd|Road|Blvd|Ln|Lane|Dr|Drive)\b")),
    ("[code]", re.compile(r"\b\d{6}\b")),  # 2FA-shaped
]


def redact(text: str) -> str:
    """Strip the obvious identifiers out of a message body."""
    for token, pattern in _PATTERNS:
        text = pattern.sub(token, text)
    return text


def redaction_hits(text: str) -> list[str]:
    return [token for token, pattern in _PATTERNS if pattern.search(text)]


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
