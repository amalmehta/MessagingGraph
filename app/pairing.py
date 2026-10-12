"""Letting one phone read this Mac, and nothing else.

Serving on the network is the one place this app gives up "nothing leaves this
machine", so it is off unless you ask for it and it is narrow when you do:

* **A short code, shown on the Mac and typed into the phone.** It lives for two
  minutes and is spent the moment it works, so a code glimpsed over a shoulder is
  no good by the time anyone else tries it.
* **A device token per phone.** Long and random, handed out once in exchange for
  the code, and revocable one device at a time. Nothing else gets in: the launch
  token stays a browser-on-this-Mac thing.
* **Rate limiting.** Six digits is small enough to guess if you are allowed to
  keep trying, so you are not: five wrong codes and pairing closes.

The tokens are kept in the vault beside everything else, so they survive a
restart but not a `mg wipe`.
"""
from __future__ import annotations

import hmac
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime

CODE_TTL = 120.0          # seconds a pairing code is worth typing
MAX_ATTEMPTS = 5          # wrong codes before pairing shuts
TOKEN_BYTES = 32


def _eq(a: str | None, b: str | None) -> bool:
    return hmac.compare_digest((a or "").encode(), (b or "").encode())


@dataclass
class Device:
    """One phone that has been let in."""
    id: str
    name: str
    token: str
    paired_at: datetime

    def public(self) -> dict:
        """What the UI may see. Never the token."""
        return {"id": self.id, "name": self.name, "paired_at": self.paired_at}


@dataclass
class Pairing:
    """The devices allowed in, and the code currently on offer."""
    devices: dict[str, Device] = field(default_factory=dict)
    _code: str | None = None
    _code_until: float = 0.0
    _attempts: int = 0
    # Set when the lockout shut the door rather than you. Someone on your Wi-Fi
    # guessing at your code is worth being told about, and without this the Mac
    # just quietly stopped offering one and looked broken.
    shut_by_guessing: bool = False

    # -- the code -------------------------------------------------------------
    def open(self) -> str:
        """Start offering a code, replacing any code already on offer."""
        self._code = f"{secrets.randbelow(1_000_000):06d}"
        self._code_until = time.monotonic() + CODE_TTL
        self._attempts = 0
        self.shut_by_guessing = False
        return self._code

    def close(self) -> None:
        self._code, self._code_until, self._attempts = None, 0.0, 0

    @property
    def offering(self) -> bool:
        return self._code is not None and time.monotonic() < self._code_until

    def seconds_left(self) -> int:
        return max(0, int(self._code_until - time.monotonic())) if self.offering else 0

    def code_on_offer(self) -> str | None:
        """The code, for showing on this Mac only -- never over the network."""
        return self._code if self.offering else None

    # -- redeeming it ---------------------------------------------------------
    def redeem(self, code: str, device_name: str) -> Device | None:
        """Swap a correct code for a device token. One use, then the code is gone."""
        if not self.offering:
            return None
        self._attempts += 1
        if self._attempts > MAX_ATTEMPTS:
            self.close()
            self.shut_by_guessing = True
            return None
        if not _eq(code.strip(), self._code):
            return None
        device = Device(
            id=secrets.token_hex(8),
            name=(device_name or "A phone").strip()[:40],
            token=secrets.token_urlsafe(TOKEN_BYTES),
            paired_at=datetime.now(),
        )
        self.devices[device.id] = device
        self.close()                      # spent
        return device

    # -- using it -------------------------------------------------------------
    def device_for(self, token: str | None) -> Device | None:
        if not token:
            return None
        for d in self.devices.values():
            if _eq(token, d.token):
                return d
        return None

    def revoke(self, device_id: str) -> bool:
        return self.devices.pop(device_id, None) is not None

    # -- storage --------------------------------------------------------------
    def to_dict(self) -> dict:
        return {"devices": [{"id": d.id, "name": d.name, "token": d.token,
                             "paired_at": d.paired_at.isoformat()}
                            for d in self.devices.values()]}

    @classmethod
    def from_dict(cls, d: dict) -> "Pairing":
        out = cls()
        for row in d.get("devices", []):
            try:
                out.devices[row["id"]] = Device(
                    row["id"], row["name"], row["token"],
                    datetime.fromisoformat(row["paired_at"]))
            except (KeyError, TypeError, ValueError):
                continue          # a row we can't read is a row we don't trust
        return out


class PairingStore:
    """Paired devices, in the vault when there is one and in memory otherwise."""

    NAME = "pairing.v1"

    def __init__(self, vault=None) -> None:
        self.vault = vault
        self._memory: Pairing | None = None

    # seal/unseal, the same small-JSON door OverrideStore uses. `read` and `write`
    # on a Vault are the corpus, not a named document, and asking them for one
    # took the server down on startup wherever a real vault existed.
    def load(self) -> Pairing:
        if self.vault is None:
            if self._memory is None:
                self._memory = Pairing()
            return self._memory
        return Pairing.from_dict(self.vault.unseal(self.NAME) or {})

    def save(self, pairing: Pairing) -> None:
        if self.vault is None:
            self._memory = pairing
            return
        self.vault.seal(self.NAME, pairing.to_dict())
