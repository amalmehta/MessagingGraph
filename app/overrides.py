"""Your own corrections: names for unnamed contacts, and verdicts on filtered threads.

For imported data these are sealed into the vault with the same Keychain key, and
keyed by the stable HMAC ids, so a name you give "(415) 555-0199" survives a fresh
`mg import`. For the demo and test sources they live in memory only.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field, replace
from datetime import datetime

from .models import Contact, Corpus, Thread
from .names import group_label, legacy_group_label, looks_like_handle

OVERRIDES_FILE = "overrides.v1.enc"
THEMES = ("auto", "light", "dark")
HIDE_DAYS = {"skip": 7, "copied": 3}
UNNAMED = re.compile(r"^\(\d{3}\) \d{3}-\d{4}$|^\+?[\d\s().-]{7,}$|^[^\s@]+@[^\s@]+$")
MAX_NAME = 60


@dataclass
class Overrides:
    names: dict[str, str] = field(default_factory=dict)       # contact id -> name you gave
    real: set[str] = field(default_factory=set)               # thread ids you restored
    automated: set[str] = field(default_factory=set)          # thread ids you hid
    locations: dict[str, str] = field(default_factory=dict)   # contact id -> place you gave
    location_unknown: set[str] = field(default_factory=set)   # contact ids you said you don't know
    # Choices the browser used to keep to itself. The Mac app serves on a new
    # random port every launch, so localStorage -- which is scoped to an origin
    # -- was a fresh empty box each time: every "Not now" and every theme choice
    # was forgotten the moment you quit. They belong with the other corrections.
    theme: str = "auto"                                       # auto | light | dark
    hidden: dict[str, dict] = field(default_factory=dict)     # draft id -> {why, at}

    def to_dict(self) -> dict:
        return {"names": self.names, "real": sorted(self.real), "automated": sorted(self.automated),
                "locations": self.locations, "location_unknown": sorted(self.location_unknown),
                "theme": self.theme, "hidden": self.hidden}

    @classmethod
    def from_dict(cls, d: dict) -> "Overrides":
        theme = d.get("theme", "auto")
        hidden = d.get("hidden", {})
        return cls(dict(d.get("names", {})), set(d.get("real", [])), set(d.get("automated", [])),
                   dict(d.get("locations", {})), set(d.get("location_unknown", [])),
                   theme if theme in THEMES else "auto",
                   {k: v for k, v in hidden.items() if isinstance(v, dict)})


class OverrideStore:
    def __init__(self, vault=None) -> None:
        self.vault = vault
        self._memory = Overrides()

    def load(self) -> Overrides:
        if self.vault is None:
            return self._memory
        data = self.vault.unseal(OVERRIDES_FILE)
        return Overrides.from_dict(data) if data else Overrides()

    def save(self, o: Overrides) -> None:
        if self.vault is None:
            self._memory = o
        else:
            self.vault.seal(OVERRIDES_FILE, o.to_dict())


def clean_name(raw: str) -> str:
    """A display name you typed: trimmed, no control characters, sensible length."""
    text = "".join(ch for ch in str(raw) if unicodedata.category(ch)[0] != "C")
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        raise ValueError("Type a name first.")
    if len(text) > MAX_NAME:
        raise ValueError(f"Keep it under {MAX_NAME} characters.")
    return text


def clean_place(raw: str) -> str:
    """A place you typed, in one consistent spelling.

    "oakland", " Oakland " and "OAKLAND" have to be the same place, or grouping people
    by location quietly splits them. Known places take their standard name ("nyc" ->
    New York); anything else typed all in lowercase is title-cased.
    """
    from .analysis.places import ALIASES
    try:
        text = clean_name(raw)
    except ValueError as e:
        raise ValueError(str(e).replace("name", "place")) from None
    known = ALIASES.get(text.lower())
    if known:
        return known
    return text.title() if text == text.lower() else text


def is_unnamed(contact: Contact) -> bool:
    return "unnamed" in contact.tags or looks_like_handle(contact.name)


def apply_names(corpus: Corpus, names: dict[str, str]) -> Corpus:
    """Rename contacts and their one-to-one threads, and rebuild group titles that
    were generated from member names -- including ones an earlier import baked in --
    so naming "(408) 555-0111" changes the group title too, not just the chip."""
    contacts = dict(corpus.contacts)
    for cid, name in names.items():
        c = contacts.get(cid)
        if c is not None:
            contacts[cid] = replace(c, name=name, tags=tuple(t for t in c.tags if t != "unnamed"))
    threads: dict[str, Thread] = {}
    for tid, t in corpus.threads.items():
        name = t.name
        if not t.is_group:
            pid = t.participant_ids[0]
            if pid in names and pid in contacts:
                name = contacts[pid].name
        else:
            before = [corpus.contacts[c].name for c in t.participant_ids if c in corpus.contacts]
            if name in (group_label(before), legacy_group_label(before)):
                name = group_label([contacts[c].name for c in t.participant_ids if c in contacts])
        threads[tid] = replace(t, name=name) if name != t.name else t
    return Corpus(contacts=contacts, threads=threads, messages=corpus.messages,
                  source_name=corpus.source_name)


MILESTONES_FILE = "milestones.v1.enc"
VERDICTS = ("confirmed", "dismissed")


class MilestoneStore:
    """Birthdays and anniversaries found in your messages, and what you said about each.

    Sealed into the vault with the same key as everything else. Detections are kept
    per platform and outlive the messages they came from, so a re-import over a
    shorter window doesn't forget a birthday; a date you confirmed is never dropped.
    Your verdicts are kept apart from the detections and keyed by the stable ids,
    so a dismissed one stays dismissed through every recompute and re-import.
    """

    def __init__(self, vault=None) -> None:
        self.vault = vault
        self._memory: dict = {}

    def load(self) -> dict:
        data = self._memory if self.vault is None else (self.vault.unseal(MILESTONES_FILE) or {})
        return {"platforms": dict(data.get("platforms", {})), "verdicts": dict(data.get("verdicts", {}))}

    def save(self, data: dict) -> None:
        if self.vault is None:
            self._memory = data
        else:
            self.vault.seal(MILESTONES_FILE, data)

    def sync(self, platform: str, detected: list[dict]) -> list[dict]:
        """Fold a fresh detection into what is stored, and return the merged set.

        A fresh reading replaces older ones for the same person and kind, unless you
        confirmed the older one. Written only when something changed.
        """
        data = self.load()
        kept = data["platforms"].get(platform, {})
        fresh = {d["id"]: d for d in detected}
        seen = {(d["contact_id"], d["kind"]) for d in detected}
        merged = dict(fresh)
        for mid, item in kept.items():
            if mid in fresh:
                continue
            if (data["verdicts"].get(mid, {}).get("status") == "confirmed"
                    or (item["contact_id"], item["kind"]) not in seen):
                merged[mid] = item
        if merged != kept:
            data["platforms"][platform] = merged
            self.save(data)
        return list(merged.values())

    def verdicts(self) -> dict[str, dict]:
        return self.load()["verdicts"]

    def decide(self, mid: str, status: str | None) -> None:
        """Confirm, dismiss, or (None) take back what you said."""
        data = self.load()
        if status is None:
            data["verdicts"].pop(mid, None)
        elif status in VERDICTS:
            data["verdicts"][mid] = {"status": status, "at": datetime.now().isoformat(timespec="seconds")}
        else:
            raise ValueError(f"unknown verdict {status!r}")
        self.save(data)
