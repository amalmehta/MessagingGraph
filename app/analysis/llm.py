"""The Claude seam.

Pass 1 computes every metric locally and never calls this. It exists now so the
boundary is real before anything needs to cross it: `draft_message` cannot reach
the network without `NetworkGate.authorize` returning, and what it sends is the
gate's redacted string rather than its own payload.

Pass 2 (drafting + the reminder queue) plugs in here.
"""
from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass

from ..models import Message, RelationshipProfile
from ..privacy import ConsentError, NetworkGate

MODEL = "claude-opus-5"
ENDPOINT = "https://api.anthropic.com/v1/messages"


@dataclass
class DraftRequest:
    profile: RelationshipProfile
    recent: list[Message]
    intent: str                 # "check in", "revive a stale plan", "reply"
    notes: str = ""


def build_payload(req: DraftRequest, max_messages: int = 24) -> str:
    """Assemble exactly what would be sent, so the UI can show it verbatim."""
    p = req.profile
    lines = [
        f"You are helping draft a text message to {p.name}.",
        f"Intent: {req.intent}.",
        "",
        "What the local analysis knows about this relationship:",
        f"- warmth {p.warmth:.0f}/100, importance {p.importance:.0f}/100, trend {p.trend}",
        f"- you send {100 * (1 - p.reciprocity):.0f}% of the messages and start "
        f"{100 * p.initiation_balance:.0f}% of conversations",
        f"- your self-editing with them scores {p.self_editing:.0f} (33 is your own baseline)",
        "",
        "Recent messages, oldest first, for voice matching:",
    ]
    for m in req.recent[-max_messages:]:
        who = "me" if m.is_from_me else p.name
        lines.append(f"{who}: {m.text}")
    if req.notes:
        lines += ["", f"User's note: {req.notes}"]
    lines += ["", "Write one message in the user's own voice. No preamble, no options."]
    return "\n".join(lines)


def draft_message(req: DraftRequest, gate: NetworkGate, api_key: str | None = None) -> str:
    """Draft via Claude. Raises ConsentError unless the user opened the gate."""
    payload = build_payload(req)
    body = gate.authorize("draft", payload)        # redacted, or raises

    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("no ANTHROPIC_API_KEY available")

    data = json.dumps({
        "model": MODEL, "max_tokens": 400,
        "messages": [{"role": "user", "content": body}],
    }).encode()
    request = urllib.request.Request(ENDPOINT, data=data, headers={
        "content-type": "application/json",
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
    })
    with urllib.request.urlopen(request, timeout=30) as resp:
        out = json.loads(resp.read())
    return "".join(part.get("text", "") for part in out.get("content", []))
