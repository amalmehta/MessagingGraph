"""Process-wide state: one loaded corpus, one settings object, one gate.

Analysis is recomputed whenever privacy settings change, since settings decide
what analysis is allowed to see in the first place.
"""
from __future__ import annotations

from datetime import datetime

from .analysis.plans import extract_plans, follow_through, open_plan_counts
from .analysis.places import infer_locations
from .analysis.relationships import profile_all
from .analysis.vibes import attach_vibes
from .analysis.spam import classify
from .ingest.base import load_source
from .models import Corpus
from .overrides import OverrideStore, apply_names
from .privacy import NetworkGate, PrivacySettings, apply_settings


class AppState:
    def __init__(self, source: str = "synthetic") -> None:
        self.source_name = source
        # Each platform is worked out once and kept, because switching between
        # them should feel like a tab and not like a reload.
        self._cache: dict[str, dict] = {}
        self.settings = PrivacySettings()
        self.gate = NetworkGate(self.settings)
        self.raw: Corpus | None = None
        self.reload()

    @property
    def platform(self) -> str:
        if self.source_name.startswith("vault:"):
            return self.source_name.split(":", 1)[1]
        return "imessage" if self.source_name == "vault" else self.source_name

    def platforms(self) -> list[str]:
        """What there is to switch between."""
        if self.source_name in ("synthetic",) or self.source_name.startswith("chatdb:"):
            return [self.source_name]
        from .vault import Vault
        return Vault().imported() or ["imessage"]

    def switch(self, platform: str) -> None:
        """Show another platform. The one being left is kept as it was, so
        coming back is instant."""
        want = "vault" if platform == "imessage" else f"vault:{platform}"
        if want == self.source_name:
            return
        self._cache[self.source_name] = self.__dict__.copy()
        self.source_name = want
        kept = self._cache.get(want)
        if kept:
            self.__dict__.update(kept)
            self.source_name = want
            return
        self.reload()

    def reload(self) -> None:
        self._cache.pop(self.source_name, None)
        self.raw = load_source(self.source_name)
        # One reference clock for the whole app, anchored to the newest message in
        # the store rather than to the wall clock. A corpus is loaded once and then
        # queried for as long as the process lives; letting "now" drift away from it
        # slowly drains the most recent bucket of every chart and reports a decline
        # that is really just the server having been up for a day.
        self.now = self.raw.messages[-1].ts if self.raw.messages else datetime.now()
        if self.source_name == "vault" or self.source_name.startswith("vault:"):
            from .vault import Vault
            self.overrides = OverrideStore(Vault())
        else:
            self.overrides = OverrideStore()
        self.recompute()

    def recompute(self) -> None:
        assert self.raw is not None
        now = self.now
        # Your corrections first, so a name you gave is what every later step sees.
        o = self.overrides.load()
        # Naming and spam classification read the store and your corrections and
        # nothing else -- not the privacy settings, which are applied further down.
        # Classifying is the single most expensive step in here, and excluding one
        # person used to pay for it again to arrive at exactly the same verdicts.
        reading = (id(self.raw), tuple(sorted(o.names.items())),
                   tuple(sorted(o.automated)), tuple(sorted(o.real)))
        if getattr(self, "_reading", None) != reading:
            self.named = apply_names(self.raw, o.names)
            self.verdicts = classify(self.named)
            self._reading = reading
        for tid, v in self.verdicts.items():
            if tid in o.automated:
                v.automated, v.decided_by = True, "you"
            elif tid in o.real:
                v.automated, v.decided_by = False, "you"
        hidden = {tid for tid, v in self.verdicts.items() if v.automated}
        threads = {k: t for k, t in self.named.threads.items() if k not in hidden}
        present = {c for t in threads.values() for c in t.participant_ids}
        self.real = Corpus(
            contacts={k: c for k, c in self.named.contacts.items() if k in present},
            threads=threads,
            messages=[m for m in self.named.messages if m.thread_id not in hidden],
            source_name=self.named.source_name)
        self.corpus = apply_settings(self.real, self.settings)
        self.plans = extract_plans(self.corpus, now)
        self.profiles = profile_all(
            self.corpus, now,
            open_plans=open_plan_counts(self.plans),
            follow_through=follow_through(self.plans),
        )
        attach_vibes(self.corpus, self.profiles, self.plans, now)
        # Where people live: your answer first, otherwise what their messages suggest.
        inferred = infer_locations(self.corpus)
        self.locations = {}
        for cid in self.corpus.contacts:
            if cid in o.locations:
                self.locations[cid] = {"place": o.locations[cid], "source": "you"}
            elif cid in inferred and cid not in o.location_unknown:
                # "Don't know" is an answer about the guess too: you've seen it and declined it.
                self.locations[cid] = {**inferred[cid], "source": "messages"}
        self.location_unknown = set(o.location_unknown)
        self.profiles_by_id = {p.contact_id: p for p in self.profiles}
        self.version = getattr(self, "version", 0) + 1     # lets derived views cache safely
