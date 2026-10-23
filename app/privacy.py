"""Privacy controls.

Design stance: the app is local-only and read-only by construction, and the only
way message content can leave the machine is through `NetworkGate`, which refuses
unless the user has explicitly opened consent for that specific purpose. Every
crossing is recorded in an audit log the UI can display.

Two other calls exist, and they go through the same gate: the licence check and
the update check. Neither carries anything from your messages, and the gate makes
sure of it rather than trusting the caller -- each is pinned to one address and
a fixed list of short fields, so there is nowhere for message text to ride along.

Nothing here is decorative -- the LLM seam in analysis/llm.py cannot make a call
without going through this module first.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta

from .models import Corpus, Message
from .product import LICENCE_API, UPDATE_URL

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
    #: Master switch for message content. While True nothing derived from your
    #: messages may leave, for any reason. The licence and update checks carry
    #: none of it and are governed separately, below.
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
    #: Ask the product site once a week whether there is a newer version.
    update_checks: bool = True

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


@dataclass(frozen=True)
class ServiceRule:
    """One outside service: where it lives and the only fields it may be sent."""
    url_prefix: str
    fields: frozenset[str]
    exact: bool = False          # the whole URL, not a prefix of it


#: Everything that may leave without carrying message content. Nothing else can.
SERVICES = {
    # Lemon Squeezy's public License API: activate, validate, deactivate.
    "licence": ServiceRule(LICENCE_API, frozenset({"license_key", "instance_id", "instance_name"})),
    # A static file on the product site. A GET with no fields at all.
    "updates": ServiceRule(UPDATE_URL, frozenset(), exact=True),
}
#: Longer than any licence key or id, far shorter than a message worth reading.
MAX_FIELD = 128


def _mask(value: str) -> str:
    return value[:4] + "…" if len(value) > 6 else "…"


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
            return False, "local_only is on; nothing from your messages may leave"
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

    def authorize_service(self, purpose: str, url: str, fields: dict | None = None) -> dict:
        """Let a licence or update call through, or raise. Returns the fields to send.

        Not subject to `local_only`, which is about message content: these have
        none, and the checks here are what make that true rather than promised.
        """
        fields = dict(fields or {})
        rule = SERVICES.get(purpose)
        allowed, reason = True, "carries no message content"
        if rule is None:
            allowed, reason = False, f"'{purpose}' is not a known service"
        elif (url != rule.url_prefix) if rule.exact else not url.startswith(rule.url_prefix):
            allowed, reason = False, f"{purpose} calls may only go to {rule.url_prefix}"
        elif set(fields) - rule.fields:
            allowed, reason = False, (f"{purpose} calls may not send "
                                      f"{', '.join(sorted(set(fields) - rule.fields))}")
        elif any(not isinstance(v, str) or len(v) > MAX_FIELD or "\n" in v
                 for v in fields.values()):
            allowed, reason = False, "a field is too long or not plain text"
        elif purpose == "updates" and not self.settings.update_checks:
            allowed, reason = False, "update checks are switched off"
        shown = ", ".join(f"{k}={_mask(v) if isinstance(v, str) else '?'}"
                          for k, v in sorted(fields.items()))
        self.audit.append(AuditEntry(
            ts=datetime.now(), purpose=purpose, allowed=allowed, reason=reason,
            chars=sum(len(str(v)) for v in fields.values()), redactions=[],
            preview=f"{url} {shown}".strip()[:280],
        ))
        if not allowed:
            raise ConsentError(reason)
        return fields

    def grant(self, purpose: str) -> None:
        self.settings.local_only = False
        self.settings.network_consent.add(purpose)

    def revoke(self, purpose: str | None = None) -> None:
        if purpose is None:
            self.settings.network_consent.clear()
            self.settings.local_only = True
        else:
            self.settings.network_consent.discard(purpose)
