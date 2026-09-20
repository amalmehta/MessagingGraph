"""Matching an Instagram export to the people you text, and what that adds.

Two separate jobs, deliberately kept apart:

* **Proposing a link.** A handle is not a person. "sofia.m" is probably Sofia
  Marino and might be someone else entirely, so nothing here links anyone on its
  own -- it proposes, shows why, and waits. A wrong merge would blend two
  people's history into one relationship reading, which is both hard to notice
  and annoying to undo.

* **Reading the tie.** Once you have confirmed a link, the export says whether
  you follow each other, and whether they are on your close-friends list. That
  is a fact about *your* edge to them. It is never evidence about how two of your
  contacts know each other -- a personal export cannot see that.

The useful part is the gap between the two layers: people you are close enough to
on Instagram to have on a close-friends list, who have not come up in your
messages for months.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime

from ..models import Corpus, RelationshipProfile, SocialGraph
from ..names import looks_like_handle

# How sure a proposed match is. Only `strong` and `likely` are ever shown.
STRONG, LIKELY = "strong", "likely"
QUIET_DAYS = 45          # no messages for this long is worth a nudge
SILENT_LIMIT = 12


def _fold(text: str) -> str:
    """Lowercase, accent-stripped letters and digits, for comparing names."""
    text = unicodedata.normalize("NFKD", str(text or ""))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _parts(name: str) -> list[str]:
    return [p for p in re.split(r"[^A-Za-z0-9]+", str(name or "")) if p]


def _match(account, contact_name: str) -> tuple[str, str] | None:
    """(confidence, why) for one candidate pairing, or None if it isn't one."""
    if looks_like_handle(contact_name):
        return None                      # a bare phone number tells us nothing
    named = account.name != account.handle      # exports leave the display name blank
    display, handle = _fold(account.name), _fold(account.handle)
    contact = _fold(contact_name)
    if not contact:
        return None

    if named and display == contact:
        return STRONG, f"“{account.name}” is exactly how you have them saved"
    if handle == contact:
        return STRONG, f"@{account.handle} matches their name exactly"

    words = _parts(contact_name)
    first, last = (words[0], words[-1]) if len(words) > 1 else (words[0] if words else "", "")
    f, l = _fold(first), _fold(last)
    if not f:
        return None

    # "sofia.m", "sofiam", "sofia_marino", "smarino" -- first name plus some of the
    # surname is the ordinary way people build a handle.
    for candidate in (handle, display if named else ""):
        if not candidate:
            continue
        if l and candidate in (f + l, l + f):
            return STRONG, f"@{account.handle} reads as {first} {last}"
        if l and candidate == f + l[0]:
            return LIKELY, f"@{account.handle} reads as {first} {last[0]}."
        if l and candidate == f[0] + l:
            return LIKELY, f"@{account.handle} reads as {first[0]}. {last}"
        if candidate == f and len(f) >= 4:
            return LIKELY, f"@{account.handle} is their first name"
    return None


def propose_links(graph: SocialGraph, corpus: Corpus, linked: dict[str, str],
                  skipped: set[str], limit: int = 20) -> list[dict]:
    """Handles that look like someone you text, best guess first.

    Anything you have already linked or set aside is gone from the list, and a
    contact who is already claimed by another handle is not offered twice.
    """
    taken = set(linked.values())
    out: list[dict] = []
    for handle, account in sorted(graph.accounts.items()):
        if handle in linked or handle in skipped or account.blocked:
            continue
        best: tuple[str, str, str] | None = None
        for cid, contact in corpus.contacts.items():
            if cid in taken:
                continue
            hit = _match(account, contact.name)
            if hit is None:
                continue
            confidence, why = hit
            # A strong match wins outright; otherwise the first likely one stands.
            if best is None or (confidence == STRONG and best[0] != STRONG):
                best = (confidence, cid, why)
                if confidence == STRONG:
                    break
        if best is None:
            continue
        confidence, cid, why = best
        out.append({
            "handle": handle, "display": account.name, "tie": account.tie,
            "close_friend": account.close_friend,
            "contact_id": cid, "contact_name": corpus.contacts[cid].name,
            "confidence": confidence, "why": why,
        })
        if len(out) >= limit:
            break
    out.sort(key=lambda r: (r["confidence"] != STRONG, r["contact_name"].lower()))
    return out


def ties_by_contact(graph: SocialGraph, linked: dict[str, str]) -> dict[str, dict]:
    """contact id -> what the export says about your edge to them."""
    out: dict[str, dict] = {}
    for handle, cid in linked.items():
        account = graph.accounts.get(handle)
        if account is None:
            continue                     # linked before a re-import dropped the handle
        out[cid] = {
            "network": graph.network, "handle": handle, "display": account.name,
            "tie": account.tie, "close_friend": account.close_friend,
            "since": account.since,
        }
    return out


def unmatched(graph: SocialGraph, linked: dict[str, str]) -> list:
    """Accounts you follow, or who follow you, that aren't anyone you text.

    Blocked accounts are left out: you have already said what you think of them.
    """
    return [a for h, a in sorted(graph.accounts.items())
            if h not in linked and not a.blocked and (a.i_follow or a.follows_me)]


def silent_ties(graph: SocialGraph, linked: dict[str, str], corpus: Corpus,
                profiles: list[RelationshipProfile], now: datetime,
                limit: int = SILENT_LIMIT) -> list[dict]:
    """People the two layers disagree about -- close on one, silent on the other.

    Sorted by how close the Instagram tie is, then by how long the quiet has run.
    A close friend you haven't texted since spring is the whole point of this.
    """
    by_id = {p.contact_id: p for p in profiles}
    ties = ties_by_contact(graph, linked)
    out: list[dict] = []
    for cid, tie in ties.items():
        p = by_id.get(cid)
        if p is None or p.last_contact is None:
            continue
        idle = (now - p.last_contact).days
        if idle < QUIET_DAYS:
            continue
        if not (tie["close_friend"] or tie["tie"] == "mutual"):
            continue
        out.append({
            "id": cid, "name": p.name, "handle": tie["handle"],
            "tie": tie["tie"], "close_friend": tie["close_friend"],
            "days_idle": idle, "state": p.state,
            "why": (f"on your close friends list, and you haven't texted in {idle} days"
                    if tie["close_friend"] else
                    f"you follow each other, and you haven't texted in {idle} days"),
        })
    out.sort(key=lambda r: (not r["close_friend"], -r["days_idle"]))
    return out[:limit]


def overview(graph: SocialGraph | None, linked: dict[str, str], corpus: Corpus) -> dict:
    """The two layers side by side: who is in both, and who is only in one."""
    if graph is None:
        return {"imported": False, "network": None, "accounts": 0}
    accounts = [a for a in graph.accounts.values() if not a.blocked]
    only_social = unmatched(graph, linked)
    texted = set(corpus.contacts)
    return {
        "imported": True,
        "network": graph.network,
        "exported_at": graph.exported_at,
        "accounts": len(accounts),
        "followers": sum(a.follows_me for a in accounts),
        "following": sum(a.i_follow for a in accounts),
        "mutuals": sum(a.tie == "mutual" for a in accounts),
        "close_friends": sum(a.close_friend for a in accounts),
        "linked": len([c for c in linked.values() if c in texted]),
        "only_social": len(only_social),
        "only_messages": len(texted - set(linked.values())),
        # A handful of names, so the number means something.
        "only_social_names": [f"@{a.handle}" for a in only_social[:6]],
    }


def enrich_profiles(profiles: list[RelationshipProfile], ties: dict[str, dict]) -> None:
    """Hang the tie off each profile so the person page can show it.

    Descriptive only. It never feeds `state`, `balance` or any score: how someone
    follows you back is not a measure of the friendship, and this app does not
    keep that kind of score.
    """
    for p in profiles:
        tie = ties.get(p.contact_id)
        if tie is not None:
            p.social = dict(tie)


def line_for(tie: dict | None) -> str:
    """One plain sentence for a tie, or "" when there is nothing to say."""
    if not tie:
        return ""
    who = f"@{tie['handle']}"
    if tie.get("close_friend"):
        return f"{who} — on your close friends list"
    return {"mutual": f"{who} — you follow each other",
            "you follow them": f"{who} — you follow them, they don't follow back",
            "they follow you": f"{who} — they follow you, you don't follow back",
            }.get(tie.get("tie"), who)
