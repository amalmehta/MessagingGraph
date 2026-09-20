"""Put a crew together for one specific thing.

"Who would I do this with?" is a different question from "who am I close to".
A Volo league needs six people who'll actually show up week after week; a trip
needs three or four who travel; a dinner needs people who like eating out. So a
crew is scored per activity:

* **interest** -- the activity matches what comes up in their thread
* **done it before** -- plans for this kind of thing that actually happened
* **closeness** -- how much you talk now
* **reliability** -- how often plans with them get a date at all
* **they gel** -- already connected to each other, so it's a crew and not a list
* **same place** -- for anything you'd do in person

Everything is local, every pick shows its reasons, and the invitation is a draft
for you to send yourself.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from ..models import Corpus, Plan, RelationshipProfile
from ..names import short_name
from .circles import activity_of
from .network import INTERESTS, interests
from .voice import day_words, greet, plan_parts, style_for, time_words, write

# Things people organise that aren't in the plan vocabulary, and the interests they lean on.
EXTRA = {
    "sports": r"\b(?:volo|league|kickball|softball|volleyball|pickleball|soccer|basketball|bowling|"
             r"frisbee|dodgeball|tennis|padel|run club|race|5k|10k|marathon)\b",
    "travel": r"\b(?:trip|travel|weekend away|road trip|ski|snowboard|cabin|camping|vacation|flights?)\b",
    "food": r"\b(?:dinner|brunch|lunch|supper club|restaurant|food|ramen|tacos|pizza|bbq|potluck)\b",
    "music": r"\b(?:concert|show|gig|festival|karaoke|band)\b",
    "games": r"\b(?:board game|game night|trivia|poker|chess|catan|d&d)\b",
    "books": r"\b(?:book club|reading group|books?)\b",
    "climbing": r"\b(?:climb\w*|boulder\w*|belay)\b",
    "hiking & outdoors": r"\b(?:hike|hiking|trail|backpacking|picnic|beach|camp\w*|kayak\w*|surf\w*)\b",
    "art & making": r"\b(?:pottery|ceramics|painting|museum|gallery|craft\w*|class)\b",
    "coffee": r"\b(?:coffee|cafe|espresso)\b",
    "cycling": r"\b(?:bike|cycling|ride)\b",
    "film & tv": r"\b(?:movie|film|cinema|screening)\b",
}
# How many people the thing actually needs.
SIZES = [(r"\b(?:volo|league|kickball|softball|volleyball|soccer|basketball|dodgeball|frisbee)\b", 6),
         (r"\b(?:book club|run club|supper club|potluck|bbq|party|game night|board game)\b", 5),
         (r"\b(?:trip|travel|road trip|ski|snowboard|cabin|camping|vacation|weekend away)\b", 4),
         (r"\b(?:dinner|brunch|lunch|picnic|hike|hiking|climb\w*|trivia|poker)\b", 4),
         (r"\b(?:concert|show|gig|movie|film|museum|gallery|coffee)\b", 3)]
SIGN_UP = re.compile(r"\b(?:league|club|class|race|5k|10k|marathon|season|team)\b", re.I)
TRIP = re.compile(r"\b(?:trip|travel|road trip|ski|snowboard|cabin|camping|vacation|weekend away)\b", re.I)
SKIP_STATES = {"new", "group"}
LANDED = {"done", "confirmed", "scheduled"}


@dataclass
class Member:
    id: str
    name: str
    score: float
    reasons: list[str] = field(default_factory=list)


@dataclass
class Crew:
    activity: str
    size: int
    members: list[Member] = field(default_factory=list)
    alternates: list[Member] = field(default_factory=list)
    place: str | None = None
    draft: str = ""
    when_label: str | None = None
    note: str = ""
    seed: int = 0            # which wording of the invitation
    crew_seed: int = 0       # which people get asked
    options: int = 3         # how many distinct wordings exist for this activity


def interests_for(activity: str) -> set[str]:
    """Which of the interests this activity leans on."""
    text = activity.lower()
    labels = {label for label, pattern in EXTRA.items() if re.search(pattern, text)}
    for label, (pattern, acts) in INTERESTS.items():
        if pattern.search(text) or text in acts:
            labels.add(label)
    return labels


def crew_size(activity: str, asked: int | None = None) -> int:
    if asked:
        return max(1, min(8, asked))
    for pattern, size in SIZES:
        if re.search(pattern, activity.lower()):
            return size
    return 4


def build_crew(corpus: Corpus, profiles: list[RelationshipProfile], plans: list[Plan],
               connections: dict, locations: dict, activity: str, now: datetime,
               size: int | None = None, nearby: bool = True, seed: int = 0,
               crew_seed: int = 0) -> Crew:
    activity = activity.strip() or "dinner"
    want = crew_size(activity, size)
    labels = interests_for(activity)
    imap = interests(corpus, plans)
    by_contact = connections.get("by_contact", {})

    # Someone you only know through a group chat can't be asked, so their interests
    # are no reason to claim the crew is interest-backed.
    known_acts = {a for l in labels for a in INTERESTS.get(l, (None, set()))[1]}
    eligible = [p for p in profiles if p.state not in SKIP_STATES]
    known_interest = any(labels & set(imap.get(p.contact_id, {})) for p in eligible)
    scored: list[tuple[float, Member, str | None]] = []
    for p in eligible:
        reasons: list[str] = []
        first = short_name(p.name)

        their = imap.get(p.contact_id, {})
        shared = [l for l in labels if l in their]
        best = min(shared, key=lambda l: (-their[l], l), default=None)
        hit = their[best] if best else 0
        interest = min(1.0, hit / 6)
        if best:
            reasons.append(f"{best} comes up in your thread with {first}")

        theirs = [pl for pl in plans if p.contact_id in pl.participant_ids]
        did = [pl for pl in theirs if pl.state in LANDED
               and (activity_of(pl).lower() in known_acts or _mentions(activity, activity_of(pl)))]
        history = min(1.0, len(did) / 3)
        if did:
            reasons.append(f"you've actually done {activity_of(did[0]).lower()} together"
                           + (f" ({len(did)} times)" if len(did) > 1 else ""))

        weight = {"close": 1.0, "steady": 0.8, "practical": 0.5, "uneven": 0.45, "quiet": 0.3}
        closeness = 0.6 * min(1.0, p.msgs_per_week_recent / 10) + 0.4 * weight.get(p.state, 0.4)
        if p.state == "close" and p.msgs_per_week_recent >= 5:
            reasons.append("you two talk most weeks")
        elif p.state == "quiet":
            reasons.append("quiet lately, so this could be a good excuse")

        reliability = 0.5 if p.follow_through is None else p.follow_through
        if p.follow_through is not None and p.follow_through >= 0.6:
            reasons.append(f"plans with {first} usually happen ({round(100 * p.follow_through)}%)")

        score = 0.32 * interest + 0.22 * history + 0.28 * closeness + 0.18 * reliability
        if not reasons:
            reasons.append("one of the people you make plans with most")
        place = (locations.get(p.contact_id) or {}).get("place")
        scored.append((score, Member(p.contact_id, p.name, round(100 * score, 1), reasons), place))

    scored.sort(key=lambda row: -row[0])
    if not scored:
        return Crew(activity, want, note="No one to suggest yet.")

    # A crew, not a leaderboard: after the first pick, favour people who already know
    # each other and (for anything in person) who are in the same place.
    in_person = not TRIP.search(activity)
    # Asking for different people has to set the last anchor aside, not just demote
    # it: the fill below re-sorts the pool by score on every pass, so anyone merely
    # moved down the list is pulled straight back into the same crew.
    offset = crew_seed % min(len(scored), 3)
    benched, pool = list(scored[:offset]), list(scored[offset:])
    chosen: list[tuple[float, Member, str | None]] = [pool.pop(0)]
    crew_place = chosen[0][2]
    while pool and len(chosen) < want:
        def fit(row):
            score, member, place = row
            known = sum(1 for c in chosen if any(x["id"] == member.id for x in by_contact.get(c[1].id, [])))
            same = 1 if (in_person and nearby and place and place == crew_place) else 0
            return score + 0.15 * min(known, 2) + 0.10 * same
        pool.sort(key=lambda row: -fit(row))
        pick = pool.pop(0)
        known_names = [short_name(c[1].name) for c in chosen
                       if any(x["id"] == pick[1].id for x in by_contact.get(c[1].id, []))]
        if known_names:
            pick[1].reasons.append(f"already knows {' and '.join(known_names[:2])}")
        if in_person and pick[2] and pick[2] == crew_place:
            pick[1].reasons.append(f"also in {pick[2]}")
        chosen.append(pick)
        crew_place = crew_place or pick[2]

    members = [row[1] for row in chosen]
    # Whoever was benched scored highest, so they are the first person to fall back on.
    alternates = [row[1] for row in benched + pool][:2]
    places = [row[2] for row in chosen if row[2]]
    crew = Crew(activity, want, members, alternates,
                place=max(set(places), key=places.count) if places else None,
                seed=seed, crew_seed=crew_seed)
    honest = ("" if known_interest else
              f" Nothing in your messages says who's into {activity}, so this is the people you "
              "make plans with most, and whose plans actually happen.")

    # The invitation, in your voice.
    style = style_for(corpus, now)
    names = [short_name(m.name) for m in members]
    hello = greet(style, "you two" if len(members) == 2 else None, group=True)
    if SIGN_UP.search(activity):
        options = [[hello, ("say", f"thinking about signing up for {activity}"), ("ask", "want in")],
                   [hello, ("ask", f"anyone up for {activity} this season"), ("say", "could use a team")],
                   [hello, ("say", f"putting together a group for {activity}"), ("ask", "who's in")]]
        crew.draft, crew.options = write(options[seed % len(options)], style), len(options)
    elif TRIP.search(activity):
        options = [[hello, ("say", f"been thinking about {activity}"), ("ask", "who's in")],
                   [hello, ("ask", f"anyone want to do {activity} this year"), ("say", "could start looking at dates")],
                   [hello, ("say", f"putting feelers out for {activity}"), ("ask", "interested")]]
        crew.draft, crew.options = write(options[seed % len(options)], style), len(options)
    else:
        when = _next_weekend(now)
        # Seeds 1 and 2 name the activity; seed 0's "it's been a minute" opener doesn't,
        # and an invitation that never says what it's for is no use. So there are two
        # wordings here, not three, and `options` has to say so or the UI cycles back
        # onto a draft the reader already turned down.
        crew.options = 2
        crew.draft = write(plan_parts(style, activity, when, now, 19, None, True, False,
                                      (seed % crew.options) + 1), style)
        crew.when_label = f"{day_words(when, now, style)} {time_words(19, style)}"
    crew.note = (f"{len(members)} people for {activity}"
                 + (f", mostly in {crew.place}" if crew.place else "")
                 + f": {', '.join(names)}." + honest)
    return crew


def _mentions(activity: str, past: str) -> bool:
    """Does `activity` name `past` as a whole word?

    Plain substring matching quietly credits the wrong history: "run" sits inside
    "brunch", so a few morning runs would read as "you've actually done run together"
    on a brunch crew.
    """
    past = past.strip().lower()
    return bool(past) and re.search(r"\b" + re.escape(past) + r"\b", activity.lower()) is not None


def _next_weekend(now: datetime) -> datetime:
    """The coming Saturday, at least three days out so people can answer."""
    from datetime import timedelta
    ahead = (5 - now.weekday()) % 7
    if ahead < 3:
        ahead += 7
    return (now + timedelta(days=ahead)).replace(hour=19, minute=0, second=0, microsecond=0)


# One natural way to say each interest, so the suggestions read like plans and not tags.
SAYS = {"games": "board game night", "climbing": "climbing", "food": "dinner",
        "hiking & outdoors": "a hike", "music": "a show", "books": "book club",
        "coffee": "coffee", "cycling": "a bike ride", "film & tv": "a movie",
        "art & making": "a pottery class", "sports": "a volo league", "travel": "a weekend trip",
        "fitness": "a run", "work & career": "dinner", "family": "dinner"}


def suggest_activities(corpus: Corpus, plans: list[Plan], profiles: list[RelationshipProfile],
                       limit: int = 6) -> list[dict]:
    """Things worth getting a crew together for, most-supported first.

    Drawn from what people actually talk about, then topped up with the two
    classics (a league, a trip) so the feature works on day one.
    """
    imap = interests(corpus, plans)
    live = {p.contact_id for p in profiles if p.state not in SKIP_STATES}
    counts: dict[str, int] = {}
    for cid, labels in imap.items():
        if cid in live:
            for label in labels:
                counts[label] = counts.get(label, 0) + 1

    out: list[dict] = []
    seen: set[str] = set()
    for label, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        said = SAYS.get(label)
        if not said or said in seen:
            continue
        seen.add(said)
        out.append({"activity": said, "size": crew_size(said),
                    "why": f"{n} {'person' if n == 1 else 'people'} you talk to about {label}"})
        if len(out) >= limit:
            break
    for said in ("a volo league", "a weekend trip"):
        if said not in seen and len(out) < limit + 2:
            out.append({"activity": said, "size": crew_size(said), "why": "a classic worth a crew"})
    return out
