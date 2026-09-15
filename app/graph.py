"""The plan graph.

Nodes are people and live plans; edges connect a person to each plan they are in,
plus person-to-person edges for people who share group threads. The result is the
"what is actually going on right now" view: clusters of people around plans, and
the plans hanging off no one because they stalled.
"""
from __future__ import annotations

from datetime import datetime

from .models import Corpus, Plan, RelationshipProfile

LIVE_STATES = {"floated", "proposed", "confirmed", "scheduled", "stale"}


STALE_WINDOW_DAYS = 90


def build_graph(corpus: Corpus, plans: list[Plan],
                profiles: list[RelationshipProfile],
                include_stale: bool = False,
                now: datetime | None = None) -> dict:
    """Live plans by default.

    A plan that stalled six months ago is history, not something going on -- so
    stale plans are opt-in, and even then only the ones recent enough to still be
    worth reviving.
    """
    now = now or datetime.now()
    states = LIVE_STATES if include_stale else LIVE_STATES - {"stale"}
    live = [p for p in plans if p.state in states
            and (p.state != "stale"
                 or (now - p.last_activity).days <= STALE_WINDOW_DAYS)]

    by_id = {p.contact_id: p for p in profiles}
    nodes: list[dict] = []
    links: list[dict] = []

    involved = {cid for p in live for cid in p.participant_ids}
    for cid, contact in corpus.contacts.items():
        prof = by_id.get(cid)
        if prof is None:
            continue
        nodes.append({
            "id": cid, "type": "person", "label": contact.name,
            "importance": prof.importance, "warmth": prof.warmth,
            "state": prof.state, "trend": prof.trend,
            "balance": prof.balance, "plans": prof.open_plans,
            "active": cid in involved,
        })

    for p in live:
        days_idle = (now - p.last_activity).days
        nodes.append({
            "id": p.id, "type": "plan", "label": p.title.split(" with ")[0],
            "title": p.title, "state": p.state, "thread": p.thread_name,
            "when": p.when_text, "days_idle": days_idle,
            "confidence": p.confidence, "size": len(p.participant_ids),
        })
        for cid in p.participant_ids:
            if cid in by_id:
                links.append({"source": cid, "target": p.id, "kind": "in_plan"})

    seen: set[tuple[str, str]] = set()
    for thread in corpus.threads.values():
        if not thread.is_group:
            continue
        members = [c for c in thread.participant_ids if c in by_id]
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                key = (a, b) if a < b else (b, a)
                if key in seen:
                    continue
                seen.add(key)
                links.append({"source": key[0], "target": key[1],
                              "kind": "shared_thread", "thread": thread.name})

    return {"nodes": nodes, "links": links,
            "counts": {"people": len(by_id), "plans": len(live),
                       "links": len(links)}}
