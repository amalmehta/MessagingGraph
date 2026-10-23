"""Local HTTP API.

Start it with `./mg serve`, which binds 127.0.0.1, turns off request logging (URLs
carry search terms), and prints the one-time sign-in link. Every request passes
through `security.Guard` first.
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .analysis.circles import activity_of, connections, plans_overview
from .analysis.crews import build_crew, suggest_activities
from .analysis.gratitude import build_gratitude
from .analysis.messages import build_messages, topic_lines
from .analysis.milestones import LABEL as MILESTONE_LABEL, upcoming
from .analysis.network import enrich, interests
from .analysis.plans import OPEN_STATES
from .analysis.proposals import propose
from .analysis.series import contact_series, overall_series, sparkline_points
from .analysis.suggestions import build as build_suggestions
from .analysis.voice import milestone_options, milestone_parts, style_for, write
from .graph import build_graph
from .names import greeting_name, short_name
from . import product
from . import updates as updating
from .appdata import AppData
from .licence import Licence, LicenceError
from .net import reachable_at
from .overrides import HIDE_DAYS, THEMES, clean_name, clean_place, is_unnamed
from .pairing import PairingStore
from .security import DEVICE_COOKIE, DEVICE_MAX_AGE, Guard
from .state import AppState


def _vault_if_any():
    """Paired phones belong with the data they can read: in the vault when the
    vault is what is being served, and in memory for the demo corpus."""
    src = os.environ.get("MG_SOURCE", "synthetic")
    if src != "vault" and not src.startswith("vault:"):
        return None
    from .vault import Vault
    return Vault()


STATIC = Path(__file__).parent / "static"

# No interactive docs: they would be one more unauthenticated-looking surface
# describing every endpoint that returns message text.
app = FastAPI(title="messaging graph", docs_url=None, redoc_url=None, openapi_url=None)
PORT = int(os.environ.get("MG_PORT", "8077"))

# Off unless `./mg serve --phone` asked for it. Everything about the network door
# -- the Host headers accepted, the pairing endpoints, the hint on the locked page
# -- hangs off this being non-empty.
LAN_HOSTS = tuple(reachable_at(PORT)) if os.environ.get("MG_PHONE") == "1" else ()
pairing_store = PairingStore(_vault_if_any())
pairing = pairing_store.load()

guard = Guard(PORT, lan_hosts=LAN_HOSTS, device_for=lambda t: pairing.device_for(t))
guard.install(app)
state = AppState(os.environ.get("MG_SOURCE", "synthetic"))
# The licence and update checks share the state's gate, so they land in the same
# audit log as anything else that ever tries to leave. The update switch is the
# one privacy setting that survives a restart, because it lives with the licence.
appdata = AppData()
licence = Licence(appdata, gate=state.gate)
state.settings.update_checks = bool(appdata.load().get("update_checks", True))

# What leaves this Mac besides nothing, said in the Privacy screen in these words.
NETWORK_USES = [
    {"purpose": "licence", "to": product.LICENCE_API.split("/")[2],
     "sends": "your licence key, this Mac's activation id and a random name made up here",
     "when": "when you activate or move a licence, then once a week while licensed. "
             "The trial makes no calls."},
    {"purpose": "updates", "to": product.UPDATE_URL.split("/")[2],
     "sends": "nothing: a plain request for one file saying what the newest version is",
     "when": f"at most once every {product.UPDATE_EVERY_DAYS} days, and never while switched off"},
]


def start_background_checks() -> None:
    """Re-check a licence that is due, and look for an update. Called when the app
    is actually serving (`mg serve`, the Mac app), never on import, so tests and
    tools that load this module make no calls."""
    import threading

    def work():
        try:
            licence.validate()
        except Exception:
            pass                # the grace period covers a check that couldn't happen
        try:
            updating.check(appdata, gate=state.gate)
        except Exception:
            pass
    threading.Thread(target=work, daemon=True).start()


def enc(obj):
    if is_dataclass(obj) and not isinstance(obj, type):
        return {k: enc(v) for k, v in asdict(obj).items()}
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: enc(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [enc(v) for v in obj]
    return obj


@app.get("/api/overview")
def overview(request: Request):
    c, plans, profiles = state.corpus, state.plans, state.profiles
    first, last = (c.span if c.messages else (datetime.now(), datetime.now()))
    open_plans = [p for p in plans if p.state in OPEN_STATES]
    stale = [p for p in plans if p.state == "stale"]
    return enc({
        "source": c.source_name,
        "messages": len(c.messages),
        "contacts": len(c.contacts),
        "threads": len(c.threads),
        "span": {"from": first, "to": last},
        "plans": {"open": len(open_plans), "stale": len(stale), "total": len(plans),
                  **plans_overview(c, plans, state.now)["counts"]},
        "states": {v: sum(1 for p in profiles if p.state == v)
                   for v in ("close", "steady", "practical", "uneven", "quiet", "new", "group")},
        "suggestions": enc_suggestions(),
        "unnamed": sum(is_unnamed(x) for x in state.real.contacts.values()),
        "filtered": sum(v.automated for v in state.verdicts.values()),
        "locations": {"unknown": sum(1 for a in location_asks(1000) if not a["suggestion"]),
                      "to_confirm": sum(1 for a in location_asks(1000) if a["suggestion"])},
        "privacy": state.settings.to_dict(),
        # Local: the licence file and the clock. Never a network call.
        "licence": licence.status().to_dict(),
        # Serving on the LAN is a different thing from the local-only switch, which
        # is about calls going out. Both belong in the footer, or it says "no
        # network" while a phone is reading over the Wi-Fi.
        "lan": {"on": guard.on_lan, "devices": len(pairing.devices),
                # "session" on this Mac, "device" on a paired phone. The screens
                # differ in the few places where the phone is deliberately not
                # allowed to do what the Mac can.
                "via": getattr(request.state, "via", None)},
    })


_network: dict = {}


def network() -> dict:
    """Group chats, mentions, interests and introductions, computed once per data version."""
    if _network.get("version") != state.version:
        base = connections(state.corpus, state.plans, state.now)
        _network.update(version=state.version,
                        value=enrich(base, state.corpus, state.plans, state.profiles))
    return _network["value"]


_interests_cache: dict = {}


def _interest_map() -> dict:
    """What each person is the one you discuss, cached with the data: the scan
    costs half a second and a search box must not."""
    if _interests_cache.get("version") != state.version:
        from .analysis.network import interests
        _interests_cache.update(version=state.version,
                                value=interests(state.corpus, state.plans))
    return _interests_cache["value"]


_topics: dict = {}


def topics() -> dict[str, str]:
    """One thing worth asking each person about. Cached with the data: scanning
    every message for interests takes half a second, and a draft gets rebuilt
    every time you press "another version"."""
    if _topics.get("version") != state.version:
        _topics.update(version=state.version,
                       value=topic_lines(interests(state.corpus, state.plans)))
    return _topics["value"]


def enc_suggestions() -> list[dict]:
    """Small, optional things you could do, newest observation first."""
    items = build_suggestions(state.profiles, state.plans, state.now)
    return [enc(i) for i in items]


@app.get("/api/contacts")
def contacts():
    """Every known contact, including excluded ones.

    The privacy screen needs the unfiltered list -- otherwise excluding someone
    removes the control that would let you undo it.
    """
    excluded = state.settings.excluded_contact_ids
    return enc([{"id": c.id, "name": c.name, "excluded": c.id in excluded}
                for c in sorted(state.real.contacts.values(), key=lambda c: c.name)])


@app.get("/api/people")
def people():
    """Profiles, each with a 20-week sparkline so the table shows trajectory.

    The connection column says how a thread is running now; the sparkline says how
    it got there, which is often the more useful half.
    """
    now = state.now
    out = []
    for p in state.profiles:
        row = enc(p)
        row["spark"] = sparkline_points(state.corpus, p.contact_id, now, weeks=20)
        row["location"] = enc(state.locations.get(p.contact_id))
        out.append(row)
    return out


@app.get("/api/series")
def series(weeks: int = 40):
    """Messaging rate across every thread, in rolling 7-day buckets."""
    return enc(overall_series(state.corpus, state.now, max(8, min(80, weeks))))


@app.get("/api/people/{cid}/series")
def person_series(cid: str, weeks: int = 40):
    if cid not in state.profiles_by_id:
        raise HTTPException(404, "No such contact.")
    return enc(contact_series(state.corpus, cid, state.now, max(8, min(80, weeks))))


class PlatformPick(BaseModel):
    platform: str


@app.get("/api/refresh")
def refresh_status():
    """When the app last went and looked, and what it found.

    Read-only here: the schedule runs in the Mac app, because reading your
    messages is something only the app has permission to do.
    """
    from .refresh import load_status
    return enc(load_status().to_dict())


@app.get("/api/platforms")
def platforms():
    """Which histories there are, and which one you are looking at."""
    from .importer import PLATFORMS as NAMES
    have = state.platforms()
    return enc({"active": state.platform,
                "platforms": [{"id": p, "label": NAMES.get(p, p.title())} for p in have]})


@app.post("/api/platform")
def set_platform(pick: PlatformPick):
    """Switch which history every view is reading.

    Each is analysed on its own: separate people, separate plans, separate
    graph. They are not merged, because a group chat on one is not the same
    room as a group chat on the other.
    """
    if pick.platform not in state.platforms():
        raise HTTPException(404, "Nothing imported from there yet.")
    state.switch(pick.platform)
    _network.clear(); _topics.clear(); _interests_cache.clear()
    return enc({"active": state.platform})


@app.get("/api/insights")
def insights():
    """The shape of the whole history, rather than one person at a time."""
    from .insights import build as build_insights
    return enc(build_insights(state.corpus, state.profiles, state.now))


@app.get("/api/search")
def search_everything(q: str = "", limit: int = 18):
    """One box for the whole app, asked in the words you would say out loud.

    Nothing here leaves the machine and nothing is sent to a model: the words
    are weighted by how rare they are in your own history, which is what makes
    "coachella" matter and "going" not.
    """
    from .search import search as run_search
    if not q.strip():
        return enc({"query": "", "terms": [], "hits": [], "searched": 0,
                    "asked_when": False, "asked_who": False})
    found = run_search(state.corpus, state.plans, state.profiles, q,
                       now=state.now, limit=max(1, min(60, limit)),
                       interests=_interest_map())
    return enc(found.to_dict())


@app.get("/api/people/{cid}/history")
def person_history(cid: str, limit: int = 60, before: str | None = None,
                   q: str | None = None):
    """Thread history, newest-last, paginated backwards from `before`.

    Each message carries the gap since the one before it *in the full thread*, so
    the UI can break the transcript into conversations even while a search filter
    is hiding the messages in between.
    """
    if cid not in state.profiles_by_id:
        raise HTTPException(404, "No such contact.")
    thread = state.corpus.direct_thread(cid)
    if thread is None:
        return {"messages": [], "total": 0, "matched": 0, "has_more": False}

    full = state.corpus.messages_in(thread.id)
    gaps: dict[str, float] = {}
    for prev, cur in zip([None, *full], full):
        gaps[cur.id] = 0.0 if prev is None else (cur.ts - prev.ts).total_seconds()

    items = full
    if q:
        from .search import fold
        needle = fold(q).strip()
        items = [m for m in items if needle in fold(m.text)]
    matched = len(items)

    if before:
        cutoff = datetime.fromisoformat(before)
        items = [m for m in items if m.ts < cutoff]

    limit = max(1, min(300, limit))
    page = items[-limit:]
    return enc({
        "messages": [{"id": m.id, "ts": m.ts, "text": m.text, "mine": m.is_from_me,
                      "reactions": list(m.reactions), "gap": gaps.get(m.id, 0.0)}
                     for m in page],
        "total": len(full),
        "matched": matched,
        "has_more": len(items) > len(page),
        "oldest": page[0].ts if page else None,
    })


@app.get("/api/people/{cid}")
def person(cid: str):
    prof = state.profiles_by_id.get(cid)
    if prof is None:
        raise HTTPException(404, "No such contact.")
    # Message bodies come from /history, which pages and searches; this endpoint
    # stays to the profile so opening a drawer does not ship a transcript twice.
    plans = [p for p in state.plans if cid in p.participant_ids]
    return enc({
        "profile": prof,
        "plans": sorted(plans, key=lambda p: p.last_activity, reverse=True)[:12],
        "threads": [t.name for t in state.corpus.threads_with(cid) if t.is_group],
        "connections": network()["by_contact"].get(cid, []),
        "thread_id": (t.id if (t := state.corpus.direct_thread(cid)) else None),
        "location": state.locations.get(cid),
        "location_unknown": cid in state.location_unknown,
        # What the message store calls them. Shown so a name you gave can be checked
        # against the number it belongs to; it is identity, not a field to edit.
        "handle": (c.handle if (c := state.real.contacts.get(cid)) else None),
    })


@app.get("/api/plans")
def plans(state_filter: str | None = None):
    items = state.plans
    if state_filter:
        wanted = set(state_filter.split(","))
        items = [p for p in items if p.state in wanted]
    return enc(items[:400])


@app.get("/api/plans/overview")
def plans_breakdown():
    """Confirmed, still being discussed, worth reviving, and the same plans by group."""
    return enc(plans_overview(state.corpus, state.plans, state.now))


@app.get("/api/plans/{pid}")
def plan_detail(pid: str):
    """One plan with the actual messages it came from, grouped by each time it came up.

    Messages that name the activity are marked, so you can see exactly what the app
    read as a plan -- and the replies around them are how it judged the plan's state.
    """
    plan = next((p for p in state.plans if p.id == pid), None)
    if plan is None:
        raise HTTPException(404, "That plan isn't in the current data any more. Reload the page.")
    thread = state.corpus.threads.get(plan.thread_id)
    by_id = {m.id: m for m in state.corpus.messages_in(plan.thread_id)}
    evidence = sorted((by_id[i] for i in plan.evidence if i in by_id), key=lambda m: m.ts)
    activity = activity_of(plan)
    names_it = re.compile(rf"\b{re.escape(activity.lower())}\b", re.I)
    mentions: list[list] = []
    for m in evidence:
        if not mentions or m.ts - mentions[-1][-1].ts > timedelta(hours=6):
            mentions.append([])
        mentions[-1].append(m)

    def sender(m) -> str:
        if m.is_from_me:
            return "You"
        c = state.corpus.contacts.get(m.sender_id)
        return c.name if c else "Someone"

    people = [state.corpus.contacts[c] for c in plan.participant_ids if c in state.corpus.contacts]
    return enc({
        "id": plan.id, "activity": activity, "state": plan.state,
        "thread_name": plan.thread_name, "is_group": bool(thread and thread.is_group),
        "people": [{"id": c.id, "name": c.name} for c in people],
        "when_text": plan.when_text, "first_seen": plan.first_seen, "last_activity": plan.last_activity,
        "days_idle": (state.now - plan.last_activity).days, "confidence": plan.confidence,
        "assumed": plan.assumed,
        "mentions": [{"started": group[0].ts, "messages": [
            {"id": m.id, "ts": m.ts, "who": sender(m), "mine": m.is_from_me, "text": m.text,
             "names_it": bool(names_it.search(m.text))} for m in group]} for group in mentions],
    })


@app.get("/api/connections")
def who_is_connected():
    """People who share a group chat, the groups themselves, and who has neither."""
    net = dict(network())
    # Group case-insensitively, so places saved with different capitalisation still meet.
    by_place: dict[str, dict] = {}
    for cid, loc in state.locations.items():
        if cid in state.corpus.contacts:
            g = by_place.setdefault(loc["place"].strip().lower(), {"place": loc["place"], "members": []})
            g["members"].append({"id": cid, "name": state.corpus.contacts[cid].name, "source": loc["source"]})
    net["places"] = sorted((g for g in by_place.values() if len(g["members"]) >= 2),
                           key=lambda g: (-len(g["members"]), g["place"]))
    return enc(net)


@app.get("/api/proposals")
def proposal(members: str, thread_id: str | None = None, seed: int = 0):
    """Draft a plan for these people from their shared history.

    A GET on purpose: it computes a suggestion and changes nothing. Sending is
    always the user's own action in their own messaging app.
    """
    ids = [m for m in members.split(",") if m]
    if not ids or any(m not in state.corpus.contacts for m in ids):
        raise HTTPException(400, "Choose at least one person from your contacts.")
    if thread_id and thread_id not in state.corpus.threads:
        raise HTTPException(404, "No such group.")
    return enc(propose(state.corpus, state.plans, ids, thread_id, max(0, seed), state.now))


@app.get("/api/crew/activities")
def crew_activities():
    """Things worth getting people together for, drawn from what they actually talk about."""
    return enc({"activities": suggest_activities(state.corpus, state.plans, state.profiles)})


@app.get("/api/crew")
def crew(activity: str, size: int | None = None, nearby: bool = True, seed: int = 0,
         crew_seed: int = 0):
    """Who to ask for one specific thing, and an invitation in your own voice.

    A GET: it works out a suggestion and changes nothing. You send it yourself.
    """
    # Free text, so keep it as typed but strip anything that isn't a plan: control
    # characters out, whitespace collapsed, length capped. It is only ever rendered
    # as text, never as markup.
    activity = re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f\x7f]", " ", activity or "")).strip()[:60]
    if not activity:
        raise HTTPException(400, "Say what the plan is.")
    if size is not None and not 1 <= size <= 8:
        raise HTTPException(400, "A crew is between 1 and 8 people.")
    return enc(build_crew(state.corpus, state.profiles, state.plans, network(), state.locations,
                          activity, state.now, size, nearby, max(0, seed), max(0, crew_seed)))


@app.get("/api/messages")
def messages_worth_sending(redraft: str | None = None, seed: int = 0):
    """Drafts ranked by how much they'd likely mean. `redraft` asks for another
    version of one draft; nothing is stored and nothing is sent."""
    seeds = {redraft: max(0, seed)} if redraft else None
    return enc(build_messages(state.corpus, state.profiles, state.plans, state.now, seeds,
                              topics=topics()))


@app.get("/api/voice")
def voice():
    """How drafts are written: traits learned from your own recent messages, nothing else."""
    style = style_for(state.corpus, state.now)
    return {"window_days": style.window_days, "messages": style.messages, "traits": style.describe()}


@app.get("/api/gratitude")
def gratitude(redraft: str | None = None, seed: int = 0):
    """Thank-you drafts tied to something that actually happened. Nothing is stored or sent."""
    seeds = {redraft: max(0, seed)} if redraft else None
    return enc(build_gratitude(state.corpus, state.profiles, state.plans, state.now, seeds))


@app.get("/api/people/{cid}/gratitude")
def person_gratitude(cid: str, seed: int = 0):
    """The best thank-you for one person, or why there isn't one yet."""
    prof = state.profiles_by_id.get(cid)
    if prof is None:
        raise HTTPException(404, "No such contact.")
    drafts = build_gratitude(state.corpus, [prof], state.plans, state.now, limit=1)
    if drafts and seed:
        drafts = build_gratitude(state.corpus, [prof], state.plans, state.now, {drafts[0].id: seed}, limit=1)
    return enc({"draft": drafts[0] if drafts else None,
                "reason": None if drafts else
                "Nothing specific to thank them for yet, and there isn't a long enough history "
                "for a general thank-you. Anything you write yourself will mean more anyway."})


class MilestonePatch(BaseModel):
    action: str                     # confirm | dismiss | undo


def _when_words(days: int) -> str:
    if days == 0:
        return "today"
    if days == 1:
        return "tomorrow"
    return f"in {days} days"


@app.get("/api/milestones")
def milestones(days: int = 30, redraft: str | None = None, seed: int = 0):
    """Birthdays and anniversaries coming up, each with a wish in your own voice.

    Read off the wall clock rather than the newest message: a birthday comes round
    on the calendar whether or not anyone has texted since the import. Nothing
    you've dismissed comes back, and nothing is sent.
    """
    today = datetime.now().date()
    items = [i for i in state.milestones if i["contact_id"] in state.corpus.contacts]
    out = []
    for i in upcoming(items, state.milestone_store.verdicts(), today, max(1, min(days, 366))):
        name = state.corpus.contacts[i["contact_id"]].name
        style = style_for(state.corpus, state.now, i["contact_id"])
        variants = milestone_options(i["kind"])
        offset = sum(map(ord, i["id"]))
        step = seed if redraft == i["id"] else 0
        thread = state.corpus.direct_thread(i["contact_id"])
        when = _when_words(i["days_until"])
        out.append({
            **i, "who": name, "label": MILESTONE_LABEL[i["kind"]],
            "headline": f"{short_name(name)}'s {MILESTONE_LABEL[i['kind']]} is {when}",
            "draft": write(milestone_parts(style, greeting_name(name), i["kind"], offset + step),
                           style, warm=True),
            "seed": step, "options": variants, "thread_id": thread.id if thread else None,
        })
    return enc(out)


@app.post("/api/milestones/{mid}")
def decide_milestone(mid: str, patch: MilestonePatch):
    """Confirm a date, dismiss it for good, or take back either. Saved with your
    other corrections (encrypted, for imported data)."""
    if not any(i["id"] == mid for i in state.milestones):
        raise HTTPException(404, "No such date.")
    status = {"confirm": "confirmed", "dismiss": "dismissed", "undo": None}
    if patch.action not in status:
        raise HTTPException(400, "Say confirm, dismiss or undo.")
    state.milestone_store.decide(mid, status[patch.action])
    return {"ok": True, "status": status[patch.action] or "detected"}


@app.get("/api/graph")
def graph(include_stale: bool = False):
    return enc(build_graph(state.corpus, state.plans, state.profiles, include_stale))


# -- privacy -----------------------------------------------------------------

class PrivacyPatch(BaseModel):
    update_checks: bool | None = None
    local_only: bool | None = None
    stats_only: bool | None = None
    redact_on_send: bool | None = None
    retention_days: int | None = None
    excluded_contact_ids: list[str] | None = None


class VerdictPatch(BaseModel):
    real: bool


class NamePatch(BaseModel):
    name: str


class LocationPatch(BaseModel):
    place: str | None = None
    unknown: bool = False




# Ask about the people who matter most first, and never about someone you've already
# answered for -- including "I don't know".
ASK_STATES = {"close", "steady", "practical", "uneven", "quiet"}


def location_asks(limit: int = 12) -> list[dict]:
    out = []
    for p in state.profiles:
        loc = state.locations.get(p.contact_id)
        if (p.state not in ASK_STATES or p.contact_id in state.location_unknown
                or (loc and loc["source"] == "you")):
            continue
        out.append({"id": p.contact_id, "name": p.name, "state": p.state, "suggestion": loc})
        if len(out) >= limit:
            break
    return out


@app.get("/api/locations/ask")
def ask_locations():
    return enc(location_asks())


@app.post("/api/contacts/{cid}/location")
def set_location(cid: str, patch: LocationPatch):
    """Set a place, say you don't know (so it stops asking), or clear your answer."""
    if cid not in state.raw.contacts:
        raise HTTPException(404, "No such contact.")
    o = state.overrides.load()
    if patch.unknown:
        o.locations.pop(cid, None)
        o.location_unknown.add(cid)
    elif not (patch.place or "").strip():
        o.locations.pop(cid, None)
        o.location_unknown.discard(cid)
    else:
        try:
            o.locations[cid] = clean_place(patch.place)
        except ValueError as e:
            raise HTTPException(400, str(e))
        o.location_unknown.discard(cid)
    state.overrides.save(o)
    state.recompute()
    return enc({"ok": True, "location": state.locations.get(cid),
                "unknown": cid in state.location_unknown})


@app.get("/api/filtered")
def filtered():
    """Threads set aside as automated or marketing, and ones you've restored, with reasons."""
    rows = []
    for tid, v in state.verdicts.items():
        if not (v.automated or v.decided_by == "you"):
            continue
        theirs = [m for m in state.named.messages_in(tid) if not m.is_from_me and m.text]
        rows.append({**enc(v), "is_group": state.named.threads[tid].is_group,
                     "sample": theirs[-1].text[:160] if theirs else ""})
    rows.sort(key=lambda r: (not r["automated"], -r["score"]))
    return {"threads": rows, "hidden": sum(r["automated"] for r in rows),
            "restored": sum(not r["automated"] for r in rows)}


@app.post("/api/threads/{tid}/verdict")
def set_verdict(tid: str, patch: VerdictPatch):
    """Your call overrides the classifier, and is saved (encrypted, for imported data)."""
    if tid not in state.named.threads:
        raise HTTPException(404, "No such conversation.")
    o = state.overrides.load()
    (o.real if patch.real else o.automated).add(tid)
    (o.automated if patch.real else o.real).discard(tid)
    state.overrides.save(o)
    state.recompute()
    return {"ok": True, "hidden": sum(v.automated for v in state.verdicts.values())}


@app.get("/api/unnamed")
def unnamed():
    """Real people with no name yet, with enough context to recognise them."""
    out = []
    for c in state.real.contacts.values():
        if not is_unnamed(c):
            continue
        t = state.real.direct_thread(c.id)
        msgs = state.real.messages_in(t.id) if t else [m for m in state.real.messages if m.sender_id == c.id]
        out.append({"id": c.id, "label": c.name, "messages": len(msgs),
                    "last": msgs[-1].ts if msgs else None,
                    "groups": [g.name for g in state.real.threads_with(c.id) if g.is_group],
                    "recent": [{"mine": m.is_from_me, "text": m.text[:140]} for m in msgs if m.text][-4:]})
    out.sort(key=lambda r: -r["messages"])
    return enc(out)


@app.post("/api/contacts/{cid}/name")
def name_contact(cid: str, patch: NamePatch):
    if cid not in state.raw.contacts:
        raise HTTPException(404, "No such contact.")
    try:
        name = clean_name(patch.name)
    except ValueError as e:
        raise HTTPException(400, str(e))
    o = state.overrides.load()
    o.names[cid] = name
    state.overrides.save(o)
    state.recompute()
    return {"ok": True, "name": name}


@app.post("/api/reload")
def reload_data():
    """Re-read the store, so an import that finished after this server started shows up.

    The Mac app's "Reload data" restarts the whole data server to do this. In a browser
    there is nothing to restart, so the state re-reads itself in place and every view
    recomputes off the new corpus.
    """
    state.reload()
    return {"ok": True, "version": state.version, "messages": len(state.corpus.messages)}


@app.get("/api/privacy")
def get_privacy():
    return enc({"settings": state.settings.to_dict(),
                "audit": [enc(a) for a in state.gate.audit[-50:]]})


@app.post("/api/privacy")
def set_privacy(patch: PrivacyPatch):
    s = state.settings
    if patch.local_only is not None:
        s.local_only = patch.local_only
        if patch.local_only:
            s.network_consent.clear()
    if patch.stats_only is not None:
        s.stats_only = patch.stats_only
    if patch.redact_on_send is not None:
        s.redact_on_send = patch.redact_on_send
    if patch.update_checks is not None:
        s.update_checks = patch.update_checks
        appdata.update(update_checks=patch.update_checks)
    # Only these two reach the corpus -- apply_settings reads excluded ids and the
    # retention window and nothing else. Local-only, stats-only and redaction govern
    # what may leave the machine, so re-reading your whole history for them is a
    # second of work that cannot change a single number on screen.
    touches_corpus = False
    if patch.retention_days is not None:
        days = max(0, patch.retention_days)
        touches_corpus |= days != s.retention_days
        s.retention_days = days
    if patch.excluded_contact_ids is not None:
        ids = set(patch.excluded_contact_ids)
        touches_corpus |= ids != s.excluded_contact_ids
        s.excluded_contact_ids = ids
    if touches_corpus:
        state.recompute()
    return enc({"settings": s.to_dict(), "contacts": len(state.corpus.contacts),
                "messages": len(state.corpus.messages)})


@app.get("/api/privacy/preview/{cid}")
def preview_outbound(cid: str):
    """Show exactly what a Claude call for this person would contain, without sending."""
    from .analysis.llm import DraftRequest, build_payload
    prof = state.profiles_by_id.get(cid)
    if prof is None:
        raise HTTPException(404, "No such contact.")
    thread = state.corpus.direct_thread(cid)
    msgs = state.corpus.messages_in(thread.id)[-24:] if thread else []
    payload = build_payload(DraftRequest(profile=prof, recent=msgs, intent="check in"))
    return enc(state.gate.preview("draft", payload))


# ------------------------------------------------------------------ the licence
class LicenceKey(BaseModel):
    key: str


def _licence_view(st) -> dict:
    d = appdata.load()
    return enc({**st.to_dict(), "network": NETWORK_USES,
                "update_checks": state.settings.update_checks,
                "update": {"latest": d.get("update_latest", ""),
                           "newer": updating.is_newer(d["update_latest"])
                           if d.get("update_latest") else False,
                           "url": d.get("update_url", ""),
                           "checked": d.get("update_checked_at", "")},
                "version": product.VERSION})


@app.get("/api/licence")
def licence_status():
    return _licence_view(licence.status())


@app.post("/api/licence/activate")
def licence_activate(body: LicenceKey, request: Request):
    _mac_only(request)
    try:
        return _licence_view(licence.activate(body.key))
    except LicenceError as e:
        raise HTTPException(400, str(e))


@app.post("/api/licence/deactivate")
def licence_deactivate(request: Request):
    _mac_only(request)
    try:
        return _licence_view(licence.deactivate())
    except LicenceError as e:
        raise HTTPException(400, str(e))


@app.post("/api/licence/check")
def licence_check(request: Request):
    _mac_only(request)
    return _licence_view(licence.validate(force=True))


# --------------------------------------------------------------- what you chose
# Theme and the drafts you waved away. These lived in localStorage, which is
# scoped to an origin -- and the Mac app serves on a new random port every
# launch, so every one of these choices was thrown away each time you opened it.
class PrefsPatch(BaseModel):
    theme: str | None = None
    hide_draft: str | None = None          # draft id to put away
    hide_why: str = "skip"                 # "skip" (7 days) or "copied" (3)
    show_draft: str | None = None          # draft id to bring back


def _prefs(o) -> dict:
    """What the browser needs: the theme, and which drafts are still put away.
    Expiry is settled here so the two ends can't disagree about the date."""
    now = time.time()
    live = {k: v for k, v in o.hidden.items()
            if now - (v.get("at", 0) / 1000) < HIDE_DAYS.get(v.get("why"), 7) * 86400}
    return {"theme": o.theme, "hidden": sorted(live)}


@app.get("/api/prefs")
def get_prefs():
    return enc(_prefs(state.overrides.load()))


@app.post("/api/prefs")
def set_prefs(patch: PrefsPatch):
    o = state.overrides.load()
    if patch.theme is not None:
        if patch.theme not in THEMES:
            raise HTTPException(400, "Theme must be auto, light or dark.")
        o.theme = patch.theme
    if patch.hide_draft:
        why = patch.hide_why if patch.hide_why in HIDE_DAYS else "skip"
        o.hidden[patch.hide_draft[:200]] = {"why": why, "at": time.time() * 1000}
    if patch.show_draft:
        o.hidden.pop(patch.show_draft, None)
    # Drop anything already expired rather than letting the list grow for ever.
    now = time.time()
    o.hidden = {k: v for k, v in o.hidden.items()
                if now - (v.get("at", 0) / 1000) < HIDE_DAYS.get(v.get("why"), 7) * 86400}
    state.overrides.save(o)
    return enc(_prefs(o))


# ------------------------------------------------------------------ the phone
class Redeem(BaseModel):
    code: str
    name: str = ""


def _mac_only(request):
    """Pairing is administered from this Mac. A paired phone is trusted with your
    messages, not with letting the next device in."""
    if getattr(request.state, "via", None) != "session":
        raise HTTPException(403, "Pair from the Mac.")


@app.get("/api/phone")
def phone_status(request: Request):
    """What the Privacy screen shows: whether the door is open, where to point the
    phone, the code if one is on offer, and who is already in."""
    _mac_only(request)
    return enc({
        "on": guard.on_lan,
        "addresses": [f"{guard.scheme}://{h}/pair" for h in LAN_HOSTS],
        "offering": pairing.offering,
        "code": pairing.code_on_offer(),
        "seconds_left": pairing.seconds_left(),
        "shut_by_guessing": pairing.shut_by_guessing,
        "devices": sorted((d.public() for d in pairing.devices.values()),
                          key=lambda d: d["paired_at"], reverse=True),
    })


@app.post("/api/phone/open")
def phone_open(request: Request):
    _mac_only(request)
    if not guard.on_lan:
        raise HTTPException(409, "Start the server with ./mg serve --phone first.")
    pairing.open()
    return phone_status(request)


@app.post("/api/phone/close")
def phone_close(request: Request):
    _mac_only(request)
    pairing.close()
    return phone_status(request)


@app.post("/api/phone/revoke/{device_id}")
def phone_revoke(device_id: str, request: Request):
    _mac_only(request)
    if pairing.revoke(device_id):
        pairing_store.save(pairing)
    return phone_status(request)


@app.post("/api/pair/redeem")
def pair_redeem(body: Redeem, request: Request):
    """The one endpoint a stranger on your network can reach. It answers the same
    way to a wrong code and to no code at all, and `pairing.py` shuts the door
    after five tries."""
    if not guard.on_lan:
        raise HTTPException(404, "Not serving on the network.")
    device = pairing.redeem(body.code, body.name or "A phone")
    if device is None:
        return JSONResponse({"ok": False, "offering": pairing.offering}, 401)
    pairing_store.save(pairing)
    resp = JSONResponse({"ok": True})
    resp.set_cookie(DEVICE_COOKIE, device.token, httponly=True, samesite="strict",
                    path="/", max_age=DEVICE_MAX_AGE, secure=guard.on_lan)
    return resp


@app.get("/pair")
def pair_page():
    return FileResponse(STATIC / "pair.html")


# Served from the root, not from /static, because a worker only controls the
# path it is served from -- at /static/sw.js it could not have cached the app
# or a single API call.
@app.get("/sw.js")
def service_worker():
    return FileResponse(STATIC / "sw.js", media_type="application/javascript")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
