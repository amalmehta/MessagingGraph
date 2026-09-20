"""Read an Instagram "Download Your Information" export.

There is no API for this. Instagram's Basic Display API -- the only one that ever
served personal accounts -- was shut down in December 2024, and the Graph API is
for business accounts and does not hand out a follower list as people. So the
only honest way in is the export you request from Instagram yourself: Settings ->
Accounts Centre -> Your information and permissions -> Download your information,
in JSON. Meta emails you a link, you download a zip, and it never leaves this Mac.

What that export contains, and the whole of what this module claims:

    connections/followers_and_following/followers_1.json     who follows you
    connections/followers_and_following/following.json       who you follow
    connections/followers_and_following/close_friends.json   your close-friends list
    connections/followers_and_following/blocked_profiles.json

Your own edges, and nothing else. It does not say how two of your contacts are
connected to each other, so this reads as a layer over your own relationships and
never as a link between two other people.

Meta has moved these files more than once and will again, so the layout is not
hard-coded: the filenames are matched anywhere in the tree, and a file that has
gone missing is a missing signal rather than a failed import.
"""
from __future__ import annotations

import json
import re
import zipfile
from datetime import datetime
from pathlib import Path

from ..models import SocialAccount, SocialGraph
from .base import SourceUnavailable

# Each file is matched on the stem, because exports number them (followers_1,
# followers_2, ...) once a list is long enough to split. Only a numeric suffix is
# a continuation: `following_hashtags.json` sits in the same folder and is a list
# of topics, not of people.
WANTED = {
    "followers": "follows_me",
    "following": "i_follow",
    "close_friends": "close_friend",
    "blocked_profiles": "blocked",
}
# The key each file hides its list under. Followers are a bare list; the rest are
# wrapped in an object with one key, and the key differs per file.
LIST_KEYS = ("relationships_following", "relationships_close_friends",
             "relationships_blocked_users", "relationships_followers")
MAX_BYTES = 64 * 1024 * 1024        # one list file; a guard against a zip bomb


def _kind(stem: str) -> str | None:
    """Which signal a file carries, ignoring the `_1`, `_2` page suffixes.

    The suffix has to be digits. Meta ships `following_hashtags.json` beside
    `following.json`, and a looser prefix match reads every hashtag you follow as
    a person you follow.
    """
    stem = stem.lower()
    for name, field in WANTED.items():
        if stem == name or re.fullmatch(re.escape(name) + r"_\d+", stem):
            return field
    return None


def _entries(blob: bytes, filename: str = "that file") -> list[dict]:
    """The rows in one export file, whichever shape it arrived in.

    A file that is *absent* is a missing signal and fine. A file that is present,
    that we picked out by name, and that holds no list we can find is something
    else: the layout has moved under us. That stops the import and says which file
    it was, because reading it as an empty list would silently report that you
    follow nobody and drop every tie on the next recompute.
    """
    try:
        data = json.loads(blob)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise SourceUnavailable(f"{filename} isn't the JSON export: {e}") from e
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in LIST_KEYS:
            if isinstance(data.get(key), list):
                return data[key]
        # An export key we have not seen: take the only list in the object rather
        # than guessing at its name. Two lists and we can't tell which is which.
        lists = [v for v in data.values() if isinstance(v, list)]
        if len(lists) == 1:
            return lists[0]
        raise SourceUnavailable(
            f"{filename} holds no list of accounts under any name this reads "
            f"({len(lists)} lists found). Instagram has changed the export layout; "
            "nothing was imported.")
    raise SourceUnavailable(
        f"{filename} isn't shaped like an export file. Nothing was imported.")


def _account(entry: dict) -> tuple[str, str, datetime | None] | None:
    """(handle, display name, when the follow started) from one row."""
    rows = entry.get("string_list_data") or []
    if not rows:
        return None
    row = rows[0]
    handle = (row.get("value") or "").strip().lstrip("@")
    if not handle:
        # Some rows carry only the profile URL.
        href = (row.get("href") or "").rstrip("/")
        handle = href.rsplit("/", 1)[-1] if href else ""
    if not handle:
        return None
    # `title` is the display name when Instagram has one, and blank more often
    # than not -- in which case the handle is the only name there is.
    name = (entry.get("title") or "").strip() or handle
    ts = row.get("timestamp")
    since = datetime.fromtimestamp(ts) if isinstance(ts, (int, float)) and ts > 0 else None
    return handle, name, since


def _files(path: Path):
    """(stem, filename, bytes) for every JSON file in the export, zipped or unpacked."""
    if path.is_dir():
        for p in sorted(path.rglob("*.json")):
            if _kind(p.stem) and p.stat().st_size <= MAX_BYTES:
                yield p.stem, p.name, p.read_bytes()
        return
    if not zipfile.is_zipfile(path):
        raise SourceUnavailable(
            f"{path.name} is neither a folder nor a zip. Point this at the export "
            "Instagram sent you, or the folder you unzipped it into.")
    with zipfile.ZipFile(path) as z:
        for info in z.infolist():
            stem = Path(info.filename).stem
            if (not info.is_dir() and info.filename.lower().endswith(".json")
                    and _kind(stem) and info.file_size <= MAX_BYTES):
                yield stem, Path(info.filename).name, z.read(info)


def load_export(path: str | Path, network: str = "instagram") -> SocialGraph:
    """Parse an export into your side of the follow graph.

    Raises only when the path itself is unusable. A file that is absent from the
    export means that signal is unknown, which is not an error: plenty of accounts
    have never put anyone on a close-friends list.
    """
    path = Path(path).expanduser()
    if not path.exists():
        raise SourceUnavailable(f"No export at {path}.")

    found: dict[str, dict] = {}
    seen_files = 0
    for stem, filename, blob in _files(path):
        field = _kind(stem)
        seen_files += 1
        for entry in _entries(blob, filename):
            parsed = _account(entry) if isinstance(entry, dict) else None
            if parsed is None:
                continue
            handle, name, since = parsed
            acc = found.setdefault(handle, {"handle": handle, "name": handle, "since": None})
            acc[field] = True
            # Prefer a real display name over a repeat of the handle, and the
            # earliest date any file agrees on.
            if name != handle:
                acc["name"] = name
            if since and (acc["since"] is None or since < acc["since"]):
                acc["since"] = since

    if not seen_files:
        raise SourceUnavailable(
            "No followers/following files in that export. Ask Instagram for your "
            "information in JSON (not HTML), and include Followers and following.")

    accounts = {h: SocialAccount(**{**{"follows_me": False, "i_follow": False,
                                       "close_friend": False, "blocked": False}, **a})
                for h, a in found.items()}
    return SocialGraph(network=network, accounts=accounts,
                       exported_at=datetime.now())


def summarize(graph: SocialGraph) -> dict:
    """Counts for the import receipt, so you can see what was read before trusting it."""
    a = graph.accounts.values()
    return {
        "network": graph.network,
        "accounts": len(graph.accounts),
        "followers": sum(x.follows_me for x in a),
        "following": sum(x.i_follow for x in a),
        "mutuals": sum(x.follows_me and x.i_follow for x in a),
        "close_friends": sum(x.close_friend for x in a),
        "blocked": sum(x.blocked for x in a),
    }
