"""Where people live, from what they've actually said.

Only statements about where someone lives count: "finally moved to Oakland",
"here in Brooklyn", "based in London". Trips don't ("flying to Denver this
weekend"). And the place has to be one this list knows, so "moved to a new team"
can't turn into a location. Anything inferred is shown as *from messages* with the
quote that suggested it, and your own answer always replaces it.
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import datetime

from ..models import Corpus, SELF_ID

_PLACES: list[tuple[str, ...]] = [
    # US cities and metros
    ("San Francisco", "san francisco", "sf", "san fran"), ("Oakland", "oakland"), ("Berkeley", "berkeley"),
    ("San Jose", "san jose"), ("Palo Alto", "palo alto"), ("Mountain View", "mountain view"),
    ("Sunnyvale", "sunnyvale"), ("Santa Clara", "santa clara"), ("Santa Cruz", "santa cruz"),
    ("Bay Area", "the bay area", "bay area", "the bay"), ("Los Angeles", "los angeles"),
    ("Long Beach", "long beach"), ("Pasadena", "pasadena"), ("Irvine", "irvine"), ("San Diego", "san diego"),
    ("Santa Barbara", "santa barbara"), ("Sacramento", "sacramento"), ("Seattle", "seattle"),
    ("Tacoma", "tacoma"), ("Spokane", "spokane"), ("Portland", "portland"), ("Eugene", "eugene"),
    ("Boise", "boise"), ("Denver", "denver"), ("Boulder", "boulder"), ("Salt Lake City", "salt lake city", "salt lake"),
    ("Las Vegas", "las vegas", "vegas"), ("Phoenix", "phoenix"), ("Tucson", "tucson"),
    ("Albuquerque", "albuquerque"), ("Austin", "austin"), ("Dallas", "dallas"), ("Houston", "houston"),
    ("San Antonio", "san antonio"), ("Chicago", "chicago"), ("Minneapolis", "minneapolis"),
    ("Milwaukee", "milwaukee"), ("Madison", "madison"), ("Detroit", "detroit"), ("Ann Arbor", "ann arbor"),
    ("Columbus", "columbus"), ("Cleveland", "cleveland"), ("Cincinnati", "cincinnati"),
    ("Indianapolis", "indianapolis"), ("St. Louis", "st. louis", "st louis", "saint louis"),
    ("Kansas City", "kansas city"), ("New York", "new york city", "new york", "nyc", "manhattan"),
    ("Brooklyn", "brooklyn"), ("Queens", "queens"), ("Jersey City", "jersey city"), ("Hoboken", "hoboken"),
    ("Boston", "boston"), ("Cambridge", "cambridge"), ("Somerville", "somerville"),
    ("Providence", "providence"), ("New Haven", "new haven"), ("Philadelphia", "philadelphia", "philly"),
    ("Pittsburgh", "pittsburgh"), ("Baltimore", "baltimore"), ("Washington, DC", "washington dc", "d.c.", "dc"),
    ("Atlanta", "atlanta"), ("Nashville", "nashville"), ("Charlotte", "charlotte"), ("Raleigh", "raleigh"),
    ("Durham", "durham"), ("Richmond", "richmond"), ("Miami", "miami"), ("Orlando", "orlando"),
    ("Tampa", "tampa"), ("New Orleans", "new orleans", "nola"), ("Honolulu", "honolulu"),
    # elsewhere
    ("London", "london"), ("Manchester", "manchester"), ("Edinburgh", "edinburgh"), ("Dublin", "dublin"),
    ("Paris", "paris"), ("Berlin", "berlin"), ("Munich", "munich"), ("Amsterdam", "amsterdam"),
    ("Barcelona", "barcelona"), ("Madrid", "madrid"), ("Lisbon", "lisbon"), ("Rome", "rome"), ("Milan", "milan"),
    ("Zurich", "zurich"), ("Geneva", "geneva"), ("Vienna", "vienna"), ("Prague", "prague"),
    ("Copenhagen", "copenhagen"), ("Stockholm", "stockholm"), ("Oslo", "oslo"), ("Tokyo", "tokyo"),
    ("Kyoto", "kyoto"), ("Osaka", "osaka"), ("Seoul", "seoul"), ("Beijing", "beijing"), ("Shanghai", "shanghai"),
    ("Hong Kong", "hong kong"), ("Taipei", "taipei"), ("Singapore", "singapore"), ("Bangkok", "bangkok"),
    ("Sydney", "sydney"), ("Melbourne", "melbourne"), ("Auckland", "auckland"), ("Toronto", "toronto"),
    ("Vancouver", "vancouver"), ("Montreal", "montreal"), ("Mexico City", "mexico city", "cdmx"),
    ("Bogotá", "bogotá", "bogota"), ("Lima", "lima"), ("Buenos Aires", "buenos aires"),
    ("São Paulo", "são paulo", "sao paulo"), ("Rio de Janeiro", "rio de janeiro"), ("Mumbai", "mumbai", "bombay"),
    ("Delhi", "new delhi", "delhi"), ("Bangalore", "bangalore", "bengaluru"), ("Chennai", "chennai"),
    ("Hyderabad", "hyderabad"), ("Pune", "pune"), ("Kolkata", "kolkata"), ("Dubai", "dubai"),
    ("Tel Aviv", "tel aviv"), ("Cairo", "cairo"), ("Nairobi", "nairobi"), ("Lagos", "lagos"),
    ("Johannesburg", "johannesburg"), ("Cape Town", "cape town"),
    # countries and US states, for "back home in India", "moved to Colorado"
    ("India", "india"), ("Japan", "japan"), ("Canada", "canada"), ("Mexico", "mexico"), ("Brazil", "brazil"),
    ("UK", "the uk", "uk", "england"), ("Ireland", "ireland"), ("France", "france"), ("Germany", "germany"),
    ("Spain", "spain"), ("Italy", "italy"), ("Portugal", "portugal"), ("Netherlands", "the netherlands", "netherlands"),
    ("Australia", "australia"), ("New Zealand", "new zealand"), ("China", "china"), ("South Korea", "south korea", "korea"),
    ("Philippines", "the philippines", "philippines"), ("Nigeria", "nigeria"), ("Kenya", "kenya"),
    ("Israel", "israel"), ("Colombia", "colombia"), ("Argentina", "argentina"),
    ("California", "california"), ("Oregon", "oregon"), ("Washington State", "washington state"),
    ("Texas", "texas"), ("Colorado", "colorado"), ("Florida", "florida"), ("Illinois", "illinois"),
    ("Massachusetts", "massachusetts"), ("Michigan", "michigan"), ("Ohio", "ohio"), ("Pennsylvania", "pennsylvania"),
    ("New Jersey", "new jersey"), ("Virginia", "virginia"), ("North Carolina", "north carolina"),
    ("Arizona", "arizona"), ("Nevada", "nevada"), ("Utah", "utah"), ("Minnesota", "minnesota"),
    ("Wisconsin", "wisconsin"), ("Tennessee", "tennessee"), ("Maryland", "maryland"), ("Hawaii", "hawaii"),
    ("Alaska", "alaska"), ("Maine", "maine"), ("Vermont", "vermont"), ("Connecticut", "connecticut"),
    ("Idaho", "idaho"), ("Montana", "montana"), ("Missouri", "missouri"), ("Louisiana", "louisiana"),
    ("New Mexico", "new mexico"), ("Rhode Island", "rhode island"), ("New Hampshire", "new hampshire"),
]
ALIASES = {alias: row[0] for row in _PLACES for alias in row[1:]}
_ALT = "|".join(re.escape(a) for a in sorted(ALIASES, key=len, reverse=True))

# Things people say about where they live, and how much each one counts.
_THEIRS = re.compile(
    r"\b(?P<cue>i live in|i'?m living in|we live in|we'?re living in|(?:just |finally |recently )?moved to|"
    r"(?:i'?m |we'?re )?moving to|relocat(?:ed|ing) to|based in|back home in|here in|"
    r"(?:my|our) (?:apartment|place|house|flat) in)\s+(?P<place>" + _ALT + r")\b", re.I)
_MINE = re.compile(r"\bhow'?s (?:life in|the weather in|everything in)\s+(?P<place>" + _ALT + r")\b", re.I)
_WEAK = ("here in", "back home in")


def infer_locations(corpus: Corpus) -> dict[str, dict]:
    """contact id -> {place, confidence, quote, when}. Newest firm statement wins."""
    evidence: dict[str, list[tuple[datetime, str, int, str]]] = {}
    direct_partner = {t.id: t.participant_ids[0] for t in corpus.threads.values() if not t.is_group}
    for m in corpus.messages:
        if not m.text:
            continue
        if m.sender_id != SELF_ID and m.sender_id in corpus.contacts:
            for hit in _THEIRS.finditer(m.text):
                cue = hit.group("cue").lower()
                weight = 1 if cue in _WEAK else 2
                evidence.setdefault(m.sender_id, []).append(
                    (m.ts, ALIASES[hit.group("place").lower()], weight, m.text))
        elif m.sender_id == SELF_ID and m.thread_id in direct_partner:
            for hit in _MINE.finditer(m.text):
                evidence.setdefault(direct_partner[m.thread_id], []).append(
                    (m.ts, ALIASES[hit.group("place").lower()], 1, m.text))

    out: dict[str, dict] = {}
    for cid, items in evidence.items():
        firm = [e for e in items if e[2] == 2]
        if firm:
            ts, place, _, quote = max(firm)
            confidence = "probably"
        else:
            counts = Counter(e[1] for e in items)
            place, n = counts.most_common(1)[0]
            ts, _, _, quote = max(e for e in items if e[1] == place)
            confidence = "probably" if n >= 2 else "likely"
        out[cid] = {"place": place, "confidence": confidence,
                    "quote": quote if len(quote) <= 140 else quote[:137] + "…", "when": ts}
    return out
