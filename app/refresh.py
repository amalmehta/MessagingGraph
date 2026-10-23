"""Keeping the data current without being asked.

The app is already running -- it sits in the menu bar and starts at login --
so it may as well read anything new once an hour instead of waiting to be
told. Two things shape how this works:

* WhatsApp needs no permission, so it refreshes quietly and always succeeds.
  iMessage needs Full Disk Access, so it may not be possible at all, and an
  daily job that cannot do its work must say so once rather than fail every
  hour in silence.
* Only platforms you have already imported are refreshed. A timer is not
  consent to start reading something new.

What it will not do is interrupt you. If the window is open the new data
waits until you are not looking at it, because having the page reload under
your hands while you are reading a draft is worse than data an hour old.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from .product import support_dir

STATE_FILE = support_dir() / "messaging-graph" / "refresh.json"
EVERY = timedelta(days=1)
# Long enough after waking that the Wi-Fi is back and the disk has settled.
FIRST_RUN_DELAY = timedelta(minutes=2)
# How often the clock is checked, which is not the same as how often it runs.
# Often enough that a Mac asleep at the appointed hour catches up on waking
# rather than waiting out another whole day.
CHECK_EVERY = timedelta(minutes=10)


@dataclass
class Outcome:
    platform: str
    ok: bool
    messages: int = 0
    note: str = ""


@dataclass
class Status:
    enabled: bool = True
    last_run: datetime | None = None
    last_results: list[Outcome] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"enabled": self.enabled,
                "last_run": self.last_run.isoformat(timespec="seconds") if self.last_run else None,
                "last_results": [{"platform": o.platform, "ok": o.ok,
                                  "messages": o.messages, "note": o.note}
                                 for o in self.last_results]}


def load_status() -> Status:
    try:
        d = json.loads(STATE_FILE.read_text())
    except Exception:
        # Never run, or written by a version that wrote something else.
        return Status()
    last = d.get("last_run")
    return Status(enabled=bool(d.get("enabled", True)),
                  last_run=datetime.fromisoformat(last) if last else None,
                  last_results=[Outcome(**o) for o in d.get("last_results", [])])


def save_status(st: Status) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(st.to_dict(), indent=2))
        STATE_FILE.chmod(0o600)
    except OSError:
        pass        # a status file that cannot be written is not worth failing over


def refresh_once(platforms: list[str] | None = None) -> list[Outcome]:
    """Re-read everything already imported. Never imports anything new."""
    from .importer import run_import, run_whatsapp_import
    from .vault import Vault

    vault = Vault()
    want = platforms if platforms is not None else vault.imported()
    out: list[Outcome] = []
    for p in want:
        # The span you chose when you imported. Falling back on the importer's
        # default turned `mg import --days 2000` into 400 days an hour later.
        days = vault.window_days(p) or 400
        try:
            if p == "whatsapp":
                r = run_whatsapp_import(days=days)
            elif p == "imessage":
                r = run_import(days=days)
            else:
                continue        # nothing can refresh an export you dragged in
            out.append(Outcome(p, r.ok, r.messages, r.note))
        except Exception as e:
            out.append(Outcome(p, False, 0, str(e) or type(e).__name__))
    return out


class Schedule:
    """A thread that wakes on its interval, reads, and tells whoever listens."""

    def __init__(self, on_done=None, should_defer=None) -> None:
        self.on_done = on_done                  # called with the outcomes, if anything landed
        self.should_defer = should_defer or (lambda: False)
        self.status = load_status()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def set_enabled(self, on: bool) -> None:
        self.status.enabled = on
        save_status(self.status)

    def due(self, now: datetime | None = None) -> bool:
        now = now or datetime.now()
        if not self.status.enabled:
            return False
        if self.status.last_run is None:
            return True
        return now - self.status.last_run >= EVERY

    def run_now(self) -> list[Outcome]:
        results = refresh_once()
        self.status.last_run = datetime.now()
        self.status.last_results = results
        save_status(self.status)
        if self.on_done and any(o.ok for o in results):
            self.on_done(results)
        return results

    def _loop(self) -> None:
        self._stop.wait(FIRST_RUN_DELAY.total_seconds())
        while not self._stop.is_set():
            try:
                if self.due() and not self.should_defer():
                    self.run_now()
            except Exception:
                pass        # a bad hour should not end the schedule
            self._stop.wait(CHECK_EVERY.total_seconds())
