"""The app's own small file: licence, trial, onboarding and update-check state.

Kept apart from the vault on purpose. `mg wipe` deletes the vault folder whole,
and a licence you paid for -- or a trial already used -- should not go with it.
Nothing in here is message data: dates, a licence key, a random install id and
a few switches. Written atomically, 0600, in a 0700 folder.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .product import support_dir

FILE = "app.json"


def default_dir() -> Path:
    return support_dir() / "Messaging Graph"


class AppData:
    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root) if root is not None else default_dir()

    @property
    def path(self) -> Path:
        return self.root / FILE

    def load(self) -> dict:
        try:
            d = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}           # never written, or written by something else
        return d if isinstance(d, dict) else {}

    def save(self, data: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        tmp = self.root / f".{FILE}.tmp"
        if tmp.exists():
            tmp.unlink()
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.path)

    def update(self, **changes) -> dict:
        """Read, change and write back. None removes a key."""
        d = self.load()
        for k, v in changes.items():
            if v is None:
                d.pop(k, None)
            else:
                d[k] = v
        self.save(d)
        return d
