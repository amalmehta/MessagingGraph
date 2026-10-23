"""The trial and the licence key.

How it works, in the order a customer meets it:

* **Trial.** The clock starts the first time the app asks (the first launch).
  For `TRIAL_DAYS` everything works. After that, what you already imported stays
  on screen -- people, plans, drafts -- and importing again waits for a licence.
  Nothing is deleted and nothing is hidden.
* **Licence.** A key bought through Lemon Squeezy is activated against its
  public License API (no secret of ours ships in the app). The answer names the
  store and product the key belongs to; a key for anything else is refused and
  its activation handed straight back.
* **Re-checking.** A licensed copy validates its key every `REVALIDATE_DAYS`, so
  a refunded or disabled key stops working. If the check can't get through --
  offline, firewalled, Lemon Squeezy down -- nothing changes for `GRACE_DAYS`
  after the last check that did. Only a definite "no" from Lemon Squeezy ends a
  licence early.

Every call goes through `NetworkGate.authorize_service`, which pins it to the
License API and to three fields: the key, the instance id Lemon Squeezy handed
back, and a random name made up on this Mac. The trial makes no calls at all.

The state lives in `appdata.AppData`, beside the vault rather than inside it, so
`mg wipe` doesn't take a paid licence with it. Deciding whether an import may run
reads that file and the clock and never the network, so the importer, the menu
bar refresh and `mg import` can all ask cheaply.
"""
from __future__ import annotations

import json
import math
import os
import secrets
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Callable, Protocol

from . import product
from .appdata import AppData
from .privacy import ConsentError, NetworkGate, PrivacySettings

TIMEOUT = 15
# A failed check is tried again after this, not on every page load.
RETRY_AFTER = timedelta(hours=6)


class LicenceError(RuntimeError):
    """One sentence, for the screen, about why a licence action didn't happen."""


# ---------------------------------------------------------------------------
# Talking to the outside, behind something a test can replace
# ---------------------------------------------------------------------------

class TransportError(RuntimeError):
    """No answer at all: offline, DNS, TLS, timeout. Not a "no"."""


@dataclass
class Response:
    status: int
    body: dict


class Transport(Protocol):
    def post(self, url: str, fields: dict, timeout: float) -> Response: ...
    def get(self, url: str, timeout: float) -> Response: ...


def _tls() -> ssl.SSLContext:
    """The default context, plus macOS's own certificate bundle.

    The bundled Python's OpenSSL looks for certificates where a Linux system keeps
    them; /etc/ssl/cert.pem is where macOS does. Loading it as well costs nothing
    when the default already worked.
    """
    ctx = ssl.create_default_context()
    if os.path.exists("/etc/ssl/cert.pem"):
        try:
            ctx.load_verify_locations(cafile="/etc/ssl/cert.pem")
        except (OSError, ssl.SSLError):
            pass
    return ctx


class UrllibTransport:
    """The real one. Standard library only: no new dependency to bundle or audit."""

    def _send(self, req: urllib.request.Request, timeout: float) -> Response:
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=_tls()) as resp:
                return Response(resp.status, _json(resp.read()))
        except urllib.error.HTTPError as e:          # an answer, just not a 200
            return Response(e.code, _json(e.read()))
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise TransportError(str(getattr(e, "reason", e)) or type(e).__name__) from None

    def post(self, url: str, fields: dict, timeout: float) -> Response:
        data = urllib.parse.urlencode(fields).encode()
        return self._send(urllib.request.Request(url, data=data, method="POST", headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": f"{product.NAME}/{product.VERSION}",
        }), timeout)

    def get(self, url: str, timeout: float) -> Response:
        return self._send(urllib.request.Request(url, headers={
            "Accept": "application/json",
            "User-Agent": f"{product.NAME}/{product.VERSION}",
        }), timeout)


def _json(raw: bytes) -> dict:
    try:
        d = json.loads(raw or b"{}")
    except ValueError:
        return {}
    return d if isinstance(d, dict) else {}


# ---------------------------------------------------------------------------
# Where things stand
# ---------------------------------------------------------------------------

@dataclass
class Status:
    #: "trial", "trial_over", "licensed", or "check_needed" (a key that has gone
    #: longer than the grace period without a check getting through).
    state: str
    can_import: bool
    message: str
    trial_days_left: int = 0
    trial_ends: str = ""
    key_hint: str = ""              # the first few characters, never the key
    last_checked: str = ""
    price_usd: int = product.PRICE_USD
    buy_url: str = product.BUY_URL

    def to_dict(self) -> dict:
        return asdict(self)


def _iso(dt: datetime | None) -> str:
    return dt.isoformat(timespec="seconds") if dt else ""


def _when(value) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


def _hint(key: str) -> str:
    return key[:4] + "…" if len(key) > 8 else "…"


class Licence:
    def __init__(self, data: AppData | None = None, transport: Transport | None = None,
                 gate: NetworkGate | None = None,
                 now: Callable[[], datetime] | None = None) -> None:
        self.data = data or AppData()
        self.transport = transport or UrllibTransport()
        # The server passes its own gate, so these calls land in the same audit
        # log as everything else. The CLI has no server, and gets a fresh one
        # with the defaults: closed for message content, as always.
        self.gate = gate or NetworkGate(PrivacySettings())
        self.now = now or datetime.now

    # -- reading ---------------------------------------------------------------
    def status(self) -> Status:
        d = self.data.load()
        now = self.now()
        started = _when(d.get("trial_started"))
        if started is None:
            started = now
            d = self.data.update(trial_started=_iso(now))
        ends = started + timedelta(days=product.TRIAL_DAYS)
        days_left = max(0, math.ceil((ends - now).total_seconds() / 86400))
        trial_ok = now < ends
        common = {"trial_days_left": days_left, "trial_ends": _iso(ends)}

        if d.get("key"):
            checked = _when(d.get("validated_at"))
            since = now - checked if checked else timedelta.max
            if since < timedelta(0):
                since = timedelta(0)            # a clock set back is not a lapse
            hint = {"key_hint": _hint(d["key"]), "last_checked": _iso(checked)}
            if since <= timedelta(days=product.GRACE_DAYS):
                return Status("licensed", True, "Licensed. Thank you for buying it.",
                              **common, **hint)
            return Status("check_needed", trial_ok,
                          f"Your licence hasn't been checked for over {product.GRACE_DAYS} days. "
                          "Connect to the internet once and it carries on; what you imported "
                          "stays readable meanwhile.", **common, **hint)

        revoked = d.get("revoked")
        if trial_ok:
            msg = (f"Free trial: {days_left} day{'s' if days_left != 1 else ''} left. Everything "
                   f"works; after that it's ${product.PRICE_USD}, once.")
            return Status("trial", True, (revoked + " " if revoked else "") + msg, **common)
        return Status("trial_over", False,
                      (revoked + " " if revoked else "")
                      + "Your trial has ended. Everything you imported stays readable; "
                        f"importing again needs a licence (${product.PRICE_USD}, once).", **common)

    def can_import(self) -> bool:
        return self.status().can_import

    def due(self) -> bool:
        """Whether a licensed copy should re-check its key now."""
        d = self.data.load()
        if not d.get("key"):
            return False
        now = self.now()
        tried = _when(d.get("last_attempt"))
        if tried and now - tried < RETRY_AFTER:
            return False
        checked = _when(d.get("validated_at"))
        return checked is None or now - checked >= timedelta(days=product.REVALIDATE_DAYS)

    # -- talking to Lemon Squeezy ----------------------------------------------
    def _call(self, action: str, fields: dict) -> Response:
        url = product.LICENCE_API + action
        try:
            sent = self.gate.authorize_service("licence", url, fields)
        except ConsentError as e:
            raise LicenceError(f"The licence check was refused on this Mac: {e}.") from None
        return self.transport.post(url, sent, TIMEOUT)

    @staticmethod
    def _ours(body: dict) -> bool:
        """The key belongs to this product, in this store."""
        meta = body.get("meta") or {}
        if product.LS_STORE_ID is not None and meta.get("store_id") != product.LS_STORE_ID:
            return False
        if product.LS_PRODUCT_ID is not None and meta.get("product_id") != product.LS_PRODUCT_ID:
            return False
        return True

    def _install_id(self) -> str:
        """A random name for this Mac, so Lemon Squeezy can count activations
        without being told the computer's name (which is often yours)."""
        d = self.data.load()
        if not d.get("install_id"):
            d = self.data.update(install_id=secrets.token_hex(8))
        return d["install_id"]

    def activate(self, key: str) -> Status:
        key = (key or "").strip()
        if not key or len(key) > 128 or any(c.isspace() for c in key):
            raise LicenceError("That doesn't look like a licence key. Paste it exactly as "
                               "it arrived in the email.")
        name = f"{product.NAME} {self._install_id()}"
        try:
            r = self._call("activate", {"license_key": key, "instance_name": name})
        except TransportError:
            raise LicenceError("Couldn't reach Lemon Squeezy to check the key. Check the "
                               "connection and try again.") from None
        body = r.body
        if r.status >= 500 or r.status == 429:
            raise LicenceError("Lemon Squeezy couldn't check the key just now. Try again "
                               "in a minute.")
        if r.status != 200 or not body.get("activated"):
            why = str(body.get("error") or "the key wasn't accepted").rstrip(".")
            raise LicenceError(f"Lemon Squeezy said: {why}.")
        instance = (body.get("instance") or {}).get("id")
        if not self._ours(body):
            # Give the activation back, so a key bought for something else
            # doesn't lose a seat for having been typed in here.
            if instance:
                try:
                    self._call("deactivate", {"license_key": key, "instance_id": instance})
                except (LicenceError, TransportError):
                    pass
            raise LicenceError(f"That key is for a different product, not {product.NAME}.")
        if not instance:
            raise LicenceError("Lemon Squeezy accepted the key but sent no activation back. "
                               "Try again.")
        now = _iso(self.now())
        self.data.update(key=key, instance_id=instance, activated_at=now, validated_at=now,
                         last_attempt=now, revoked=None, last_error=None)
        return self.status()

    def validate(self, force: bool = False) -> Status:
        """Re-check the key if it is due (or `force`). Never raises for a network
        problem: the grace period is exactly for that."""
        d = self.data.load()
        if not d.get("key") or not (force or self.due()):
            return self.status()
        now = _iso(self.now())
        try:
            r = self._call("validate", {"license_key": d["key"],
                                        "instance_id": d.get("instance_id", "")})
        except (TransportError, LicenceError) as e:
            self.data.update(last_attempt=now, last_error=str(e) or "no answer")
            return self.status()
        if r.status >= 500 or r.status == 429:
            self.data.update(last_attempt=now, last_error=f"HTTP {r.status}")
            return self.status()
        body = r.body
        state = (body.get("license_key") or {}).get("status")
        if r.status == 200 and body.get("valid") and self._ours(body) \
                and state not in ("expired", "disabled"):
            self.data.update(validated_at=now, last_attempt=now, last_error=None)
            return self.status()
        # A definite no: refunded, disabled, deactivated from the dashboard.
        self._forget(now, "Your licence is no longer active (refunded, disabled or moved "
                          "to another Mac).")
        return self.status()

    def deactivate(self) -> Status:
        """Hand this Mac's activation back, to move the licence to another one."""
        d = self.data.load()
        if not d.get("key"):
            return self.status()
        try:
            r = self._call("deactivate", {"license_key": d["key"],
                                          "instance_id": d.get("instance_id", "")})
        except TransportError:
            raise LicenceError("Couldn't reach Lemon Squeezy, so the licence is still on "
                               "this Mac. Try again when you're online.") from None
        if r.status >= 500 or r.status == 429:
            raise LicenceError("Lemon Squeezy couldn't take the licence back just now. "
                               "Try again in a minute.")
        # Deactivated, or Lemon Squeezy has no record of this activation anyway:
        # either way it is not this Mac's any more.
        self._forget(_iso(self.now()), None)
        return self.status()

    def _forget(self, now: str, why: str | None) -> None:
        self.data.update(key=None, instance_id=None, activated_at=None, validated_at=None,
                         last_error=None, last_attempt=now, revoked=why)


def import_allowed(data: AppData | None = None) -> tuple[bool, str]:
    """Whether a new import may run, and if not, the sentence saying why.
    Local only: reads the licence file and the clock."""
    st = Licence(data=data).status()
    return st.can_import, ("" if st.can_import else st.message)
