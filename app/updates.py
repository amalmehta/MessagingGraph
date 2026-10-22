"""Is there a newer version?

The app is sold outside the App Store, so nothing tells a customer about a fix
unless the app asks. It asks the product site for one static file, at most once
a week, with no fields, cookies or identifiers -- the gate refuses the call
otherwise -- and only ever says "there's a newer one, here's the page". It never
downloads or installs anything itself.

Switched off under Privacy, it makes no call at all.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Callable

from . import product
from .appdata import AppData
from .licence import TIMEOUT, Transport, TransportError, UrllibTransport
from .privacy import ConsentError, NetworkGate, PrivacySettings


@dataclass
class UpdateInfo:
    checked: bool                   # whether a check happened this time
    newer: bool = False
    latest: str = ""
    url: str = ""
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _parts(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3]) or (0,)


def is_newer(latest: str, current: str = product.VERSION) -> bool:
    return _parts(latest) > _parts(current)


def check(data: AppData | None = None, transport: Transport | None = None,
          gate: NetworkGate | None = None, force: bool = False,
          now: Callable[[], datetime] | None = None) -> UpdateInfo:
    data = data or AppData()
    gate = gate or NetworkGate(PrivacySettings())
    now = (now or datetime.now)()
    d = data.load()
    last = d.get("update_checked_at")
    if not force and last:
        try:
            if now - datetime.fromisoformat(last) < timedelta(days=product.UPDATE_EVERY_DAYS):
                latest = d.get("update_latest", "")
                return UpdateInfo(False, is_newer(latest) if latest else False, latest,
                                  d.get("update_url", ""))
        except ValueError:
            pass
    if ".example" in product.UPDATE_URL:
        # No site yet: a reserved name that never resolves, so don't even try.
        return UpdateInfo(False, note="no update site configured yet")
    try:
        gate.authorize_service("updates", product.UPDATE_URL)
    except ConsentError as e:
        return UpdateInfo(False, note=str(e))
    try:
        r = (transport or UrllibTransport()).get(product.UPDATE_URL, TIMEOUT)
    except TransportError as e:
        return UpdateInfo(False, note=f"couldn't reach the site: {e}")
    data.update(update_checked_at=now.isoformat(timespec="seconds"))
    latest = str(r.body.get("version") or "") if r.status == 200 else ""
    url = str(r.body.get("url") or product.SITE_URL)
    # Only a page on the product's own site is ever offered as a link.
    if not url.startswith(product.SITE_URL):
        url = product.SITE_URL
    if not latest:
        return UpdateInfo(True, note=f"the site answered {r.status}")
    data.update(update_latest=latest, update_url=url)
    return UpdateInfo(True, is_newer(latest), latest, url)
