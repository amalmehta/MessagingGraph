"""Connections beyond group chats.

Three kinds, each carrying its evidence:

* **Mentions** -- someone comes up by name in your chat with someone else, at
  least twice. Names that are also ordinary words (Will, May, Iris) are skipped,
  and a first name shared by two contacts only counts as the full name.
* **Shared interests** -- topics that recur in a person's thread, and the
  activities you actually make plans for together.
* **Might get along** -- people who aren't connected yet but share interests and
  a similar vibe. A suggestion only: introducing people tells each about the
  other, and that's always your call.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict

from ..models import Corpus, Plan, RelationshipProfile
from ..names import looks_like_handle, short_name
from .circles import activity_of

# What each interest sounds like, in words rather than in regex: the pattern is
# built from them below, and the same words are what lets a message be ruled out
# without running the pattern at all.
WORDS: list[tuple[str, list[str], set[str]]] = [
    ("climbing", ["climb", "climbing", "boulder", "bouldering", "crag"],
     {"climbing gym", "climbing"}),
    ("hiking & outdoors", ["hike", "hikes", "hiking", "trail", "trails", "camping",
                           "backpacking", "picnic", "beach"],
     {"hike", "hiking", "camping", "picnic", "beach"}),
    ("running", ["running", "marathon", "5k", "10k", "my run"], {"run"}),
    ("food", ["ramen", "sushi", "restaurant", "brunch", "recipe", "cooking", "baking",
              "farmers market", "taco", "tacos"],
     {"ramen", "farmers market", "market"}),
    ("music", ["concert", "gig", "album", "playlist", "karaoke", "setlist", "the fillmore"],
     {"concert", "gig", "show", "karaoke"}),
    ("film & tv", ["movie", "film", "netflix", "episode", "trailer"], {"movie", "film"}),
    ("games", ["board game", "board games", "trivia", "chess", "video game", "video games",
               "game night", "catan"],
     {"board game night", "board games", "game night", "trivia"}),
    ("books", ["book club", "novel", "finished the book", "finished that book"], set()),
    ("art & making", ["museum", "gallery", "pottery", "ceramics", "painting", "exhibit"],
     {"museum", "pottery class", "pottery"}),
    ("cycling", ["bike", "cycling"], {"bike ride"}),
    ("coffee", ["latte", "espresso", "pour-over", "pour over", "coffee shop", "roaster"], set()),
    ("travel", ["trip", "flight", "travel", "traveling", "vacation", "cabin", "passport"],
     {"trip", "cabin"}),
    ("sports", ["soccer", "basketball", "football", "baseball", "tennis", "golf", "pickleball"],
     set()),
    ("pets", ["dog", "puppy", "the cat", "kitten"], set()),
]

INTERESTS: dict[str, tuple[re.Pattern[str], set[str]]] = {
    label: (re.compile(r"\b(?:" + "|".join(re.escape(w) for w in words) + r")\b", re.I), acts)
    for label, words, acts in WORDS}

TOKEN = re.compile(r"\w+")

# "the cat" can only match a message containing "cat", so the word to look for is
# "cat" and not "the" -- which is in nearly every message and would rule nothing out.
COMMON = {"the", "that", "my", "a", "an", "of", "to"}


def _lookfor(phrase: str) -> str:
    """The one word of a phrase worth looking for: the longest that isn't filler."""
    parts = TOKEN.findall(phrase.casefold())
    return max([p for p in parts if p not in COMMON] or parts, key=len)


# word -> the interests it could be evidence for. A pattern can only match text
# containing its own words, so a message whose words appear here for no interest
# cannot match any of them -- and 96 in 100 messages are about none of these
# things. Running all fourteen patterns anyway cost a second and a half of every
# page that asks who likes what; a dict lookup per word costs almost nothing.
# Casefolding only ever adds matches, so this never skips a real one.
BY_WORD: dict[str, set[str]] = {}
for _label, _words, _ in WORDS:
    for _w in _words:
        BY_WORD.setdefault(_lookfor(_w), set()).add(_label)


# First names that are also everyday words -- matching them would invent links.
WORDISH = set("""will may june april august grace hope joy faith rose mark bill frank art pat max ray
sky summer amber dawn chase hunter rich sandy iris jack chance dean drew eve gene glen guy heather
holly ivy jade jean kit lane lily miles penny reed rob rod rusty sue wade woody don carol brook cliff
crystal ginger harmony honey raven river robin ruby sage star storm violet autumn bo cash clay cole
dale dash fox gray hazel jay king lark page pearl piper sunny true wren sonny man ace tank""".split())

TRAIT_WORDS = {"playful": "playful", "tender": "warm", "lively": "lively", "deep": "deep",
               "steady": "steady", "practical": "practical", "planning": "full of plans",
               "night_owl": "late-night"}


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


LANDED = {"done", "confirmed", "scheduled"}


_INTEREST_CACHE: dict[tuple, dict[str, dict[str, int]]] = {}
_INTEREST_MAX = 4


def interests(corpus: Corpus, plans: list[Plan]) -> dict[str, dict[str, int]]:
    """contact id -> {interest: evidence}, keeping only what's distinctive for that person.

    Plans count double, but only ones that happened. Generic plans (dinner, drinks,
    coffee, a walk) aren't interests at all. And an interest has to stand out for
    this person against your other contacts: with enough history everyone you make
    plans with "likes food", which tells you nothing about who'd get along.
    """
    last = corpus.messages[-1] if corpus.messages else None
    key = (corpus.source_name, len(corpus.messages), last.id if last else "",
           len(corpus.contacts), len(plans))
    got = _INTEREST_CACHE.get(key)
    if got is not None:
        return got
    scores: dict[str, Counter] = defaultdict(Counter)
    for cid in corpus.contacts:
        t = corpus.direct_thread(cid)
        if not t:
            continue
        for m in corpus.messages_in(t.id):
            if not m.text:
                continue
            maybe: set[str] = set()
            for w in TOKEN.findall(m.text.casefold()):
                got = BY_WORD.get(w)
                if got:
                    maybe |= got
            for label in maybe:
                if INTERESTS[label][0].search(m.text):
                    scores[cid][label] += 1
    for p in plans:
        if p.state not in LANDED:
            continue
        a = activity_of(p).lower()
        for label, (_, acts) in INTERESTS.items():
            if a in acts:
                for cid in p.participant_ids:
                    scores[cid][label] += 2

    totals = {cid: sum(c.values()) for cid, c in scores.items()}
    pool = [cid for cid, t in totals.items() if t >= 5]
    mean = {label: sum(scores[cid][label] / totals[cid] for cid in pool) / len(pool)
            for label in INTERESTS} if pool else {}
    out: dict[str, dict[str, int]] = {}
    for cid, c in scores.items():
        ranked = []
        for label, count in c.items():
            if count < 3:
                continue
            lift = (count / totals[cid]) / mean[label] if mean.get(label) else 2.0
            if lift >= 1.3:
                ranked.append((lift, count, label))
        ranked.sort(reverse=True)
        if ranked:
            out[cid] = {label: count for _, count, label in ranked[:3]}
    if len(_INTEREST_CACHE) >= _INTEREST_MAX:
        _INTEREST_CACHE.pop(next(iter(_INTEREST_CACHE)))
    _INTEREST_CACHE[key] = out
    return out


def mentions(corpus: Corpus) -> dict[tuple[str, str], dict]:
    """(a, b) -> {count, chats}: a named in your one-to-one chat with b, or the reverse."""
    firsts = Counter(c.name.split()[0].lower() for c in corpus.contacts.values()
                     if c.name.split() and c.name[0].isalpha())
    lookup: dict[str, list[str]] = defaultdict(list)
    for cid, c in corpus.contacts.items():
        parts = c.name.split()
        if not parts or not parts[0][0].isalpha():
            continue                                  # unnamed numbers can't be mentioned
        first = parts[0].lower()
        if len(first) < 3 or first in WORDISH:
            continue
        if firsts[first] > 1:
            if len(parts) >= 2:
                lookup[" ".join(p.lower() for p in parts[:2])].append(cid)
        else:
            lookup[first].append(cid)
    if not lookup:
        return {}
    combined = re.compile(r"\b(" + "|".join(re.escape(k) for k in sorted(lookup, key=len, reverse=True)) + r")\b")
    links: dict[tuple[str, str], dict] = {}
    for t in corpus.threads.values():
        if t.is_group:
            continue
        partner = t.participant_ids[0]
        for m in corpus.messages_in(t.id):
            if not m.text:
                continue
            for key in {k.lower() for k in combined.findall(m.text.lower())}:
                for cid in lookup.get(key, []):
                    if cid == partner:
                        continue
                    pair = tuple(sorted((cid, partner)))
                    entry = links.setdefault(pair, {"count": 0, "chats": set()})
                    entry["count"] += 1
                    entry["chats"].add(partner)
    return links


def interest_groups(corpus: Corpus, imap: dict[str, dict[str, int]]) -> list[dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for cid, ints in imap.items():
        if cid in corpus.contacts:
            for label, score in ints.items():
                groups[label].append({"id": cid, "name": corpus.contacts[cid].name, "score": score})
    out = [{"interest": k, "members": sorted(v, key=lambda x: -x["score"])}
           for k, v in groups.items() if len(v) >= 2]
    return sorted(out, key=lambda g: (-len(g["members"]), g["interest"]))


def introductions(corpus: Corpus, profiles: list[RelationshipProfile], connected: set[tuple[str, str]],
                  imap: dict[str, dict[str, int]], limit: int = 4) -> list[dict]:
    # Only people you've named: you can't introduce someone you haven't identified.
    eligible = [p for p in profiles if p.state in ("close", "steady") and p.contact_id in corpus.contacts
                and not looks_like_handle(p.name)]
    cands = []
    for i, a in enumerate(eligible):
        for b in eligible[i + 1:]:
            if tuple(sorted((a.contact_id, b.contact_id))) in connected:
                continue
            ia, ib = imap.get(a.contact_id, {}), imap.get(b.contact_id, {})
            shared = sorted(set(ia) & set(ib), key=lambda k: -(ia[k] + ib[k]))
            if not shared:
                continue
            traits = [t for t in (a.vibe.top if a.vibe else []) if b.vibe and t in b.vibe.top]
            cands.append((2 * len(shared) + len(traits) + (a.importance + b.importance) / 200, a, b, shared, traits))
    cands.sort(key=lambda x: -x[0])
    used: Counter = Counter()
    out = []
    for score, a, b, shared, traits in cands:
        if used[a.contact_id] >= 2 or used[b.contact_id] >= 2:
            continue
        used[a.contact_id] += 1
        used[b.contact_id] += 1
        A, B = short_name(a.name), short_name(b.name)
        why = f"You talk about {_join(shared[:2])} with both of them"
        if traits:
            why += f", and both threads are {_join([TRAIT_WORDS[t] for t in traits])}"
        why += ". They don't share a group chat, and neither has come up in your chats with the other."
        out.append({
            "a": {"id": a.contact_id, "name": a.name}, "b": {"id": b.contact_id, "name": b.name},
            "shared": shared, "traits": traits, "why": why, "score": round(score, 2),
            "draft": (f"Hey {A} and {B}! Making an introduction, because I think you two would "
                      f"get along. You're both into {shared[0]}. {A}, meet {B}. {B}, meet {A}."),
        })
        if len(out) >= limit:
            break
    return out


def enrich(base: dict, corpus: Corpus, plans: list[Plan],
           profiles: list[RelationshipProfile] | None) -> dict:
    """Add mentions, interests and introductions to the group-chat connections."""
    ment = mentions(corpus)
    imap = interests(corpus, plans)
    by_contact = {cid: [dict(x) for x in lst] for cid, lst in base["by_contact"].items()}
    connected = {tuple(sorted((cid, x["id"]))) for cid, lst in by_contact.items() for x in lst}
    strong = {pair: info for pair, info in ment.items() if info["count"] >= 2}
    for (a, b), info in strong.items():
        connected.add((a, b))
        chats = _join([short_name(corpus.contacts[c].name) for c in sorted(info["chats"])])
        label = f"mentioned in your chat with {chats} ({info['count']}×)"
        for x, y in ((a, b), (b, a)):
            lst = by_contact.setdefault(x, [])
            entry = next((e for e in lst if e["id"] == y), None)
            if entry is None:
                lst.append({"id": y, "name": corpus.contacts[y].name, "via": [label]})
            elif label not in entry["via"]:
                entry["via"] = entry["via"] + [label]
    solo = sorted((c.name for cid, c in corpus.contacts.items() if not by_contact.get(cid)), key=str.lower)
    return {**base, "by_contact": by_contact, "solo": solo, "mention_links": len(strong),
            "interests": interest_groups(corpus, imap),
            "top_interests": {cid: list(v) for cid, v in imap.items() if v},
            "introductions": introductions(corpus, profiles or [], connected, imap)}
