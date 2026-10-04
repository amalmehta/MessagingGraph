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
        self.settings = PrivacySettings()
        self.gate = NetworkGate(self.settings)
        self.raw: Corpus | None = None
        self.reload()

    def reload(self) -> None:
        self.raw = load_source(self.source_name)
        # One reference clock for the whole app, anchored to the newest message in
        # the store rather than to the wall clock. A corpus is loaded once and then
        # queried for as long as the process lives; letting "now" drift away from it
        # slowly drains the most recent bucket of every chart and reports a decline
        # that is really just the server having been up for a day.
        self.now = self.raw.messages[-1].ts if self.raw.messages else datetime.now()
        if self.source_name == "vault":
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
        self.named = apply_names(self.raw, o.names)
        self.verdicts = classify(self.named)
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
