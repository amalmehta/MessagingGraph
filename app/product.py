"""What is being offered, in one place.

Whether it is free, the price and trial for when it isn't, the app's identity
and where the two outside services live. The
packaging script, the licence check, the onboarding screen and the checks in
validate.py all read from here, so changing the price or the trial is one edit.
The product site is plain HTML and cannot import this file; validate.py checks
it says the same price.

Nothing here is a secret. Lemon Squeezy's License API is public: it takes a
licence key a customer typed, and needs no API key of ours.
"""
from __future__ import annotations

import os
from pathlib import Path

NAME = "Messaging Graph"
VERSION = "1.1.0"
# macOS files Full Disk Access, the Keychain's trust and the login item under
# this. Change it after the first sale and every customer has to grant Full Disk
# Access again, so settle it before go-live and then leave it alone.
BUNDLE_ID = "com.messaginggraph.app"
COPYRIGHT = "© 2026 Messaging Graph"
# The oldest macOS the bundled Python and PyObjC run on.
MIN_MACOS = "13.0"

# -- the offer ---------------------------------------------------------------
# Free for everyone: no trial, nothing locked, no Buy button. The licence code
# stays for a paid Pro version later; set False and fill in the store to turn it on.
FREE = True
# The rest of the offer only counts once FREE is False.
PRICE_USD = 5                       # one-time (was 29; set to 5 by the owner on 2026-10-22)
TRIAL_DAYS = 14                     # everything works during it
# After the trial what you already imported stays readable; new imports wait
# for a licence. Nothing is deleted and nothing locks you out of your own data.

# -- the licence ---------------------------------------------------------------
LICENCE_API = "https://api.lemonsqueezy.com/v1/licenses/"
# From the Lemon Squeezy dashboard, once the product exists (see docs/GO-LIVE.md).
# A key bought for anything else in any store is refused once these are set; left
# as None, any valid Lemon Squeezy key would activate, which is fine for a dry
# run and wrong for a launch -- `scripts/package.sh` refuses a release build then.
LS_STORE_ID: int | None = None
LS_PRODUCT_ID: int | None = None
# How often a licensed copy re-checks its key, and how long it keeps working
# without being able to (a plane, a firewall, Lemon Squeezy having a bad day).
REVALIDATE_DAYS = 7
GRACE_DAYS = 30

# -- the site ------------------------------------------------------------------
# Placeholders until there is a domain and a checkout. `.example` is reserved and
# never resolves, so a build made before then cannot send its update check to a
# stranger's server; `release_blockers()` stops a release build that still has them.
SITE_URL = "https://messaging-graph.example"
BUY_URL = "https://REPLACE-WITH-YOUR-STORE.lemonsqueezy.com/buy/REPLACE-WITH-VARIANT-ID"
# A static file on the product site: {"version": "1.2.0", "url": "..."}.
# Fetched at most once a week, with nothing about you in the request.
UPDATE_URL = f"{SITE_URL}/version.json"
UPDATE_EVERY_DAYS = 7


def support_dir() -> Path:
    """Where the app keeps its files: ~/Library/Application Support.

    `MG_HOME` moves all of it -- the vault, the licence file, the refresh status
    -- somewhere else, which is how a packaged build is tried out on the demo
    without going near the data or Keychain item of a copy already in use.
    """
    home = os.environ.get("MG_HOME")
    return Path(home) if home else Path.home() / "Library" / "Application Support"


def release_blockers() -> list[str]:
    """What still has to be filled in before a build can be sold.

    A test build doesn't care. A release build that shipped with any of these
    would accept keys bought for other products, or send buyers to a checkout
    that doesn't exist. A free build sells nothing, so none of them apply; the
    update check already stays off while SITE_URL is a placeholder.
    """
    out = []
    if FREE:
        return out
    if LS_STORE_ID is None or LS_PRODUCT_ID is None:
        out.append("LS_STORE_ID and LS_PRODUCT_ID in app/product.py are not set")
    for name, url in (("SITE_URL", SITE_URL), ("BUY_URL", BUY_URL)):
        if "REPLACE" in url or ".example" in url:
            out.append(f"{name} in app/product.py is still a placeholder")
    return out


if __name__ == "__main__":       # `python -m app.product` -- for scripts/package.sh
    import sys
    if "--check-release" in sys.argv:
        for b in release_blockers():
            print(f"  xx  {b}")
        sys.exit(1 if release_blockers() else 0)
    print(VERSION)
