"""What a customer's Mac meets: the bundle, its signature, the first-run screen,
the product site and the one place the price is set.

Run from validate.py, which passes in its `check`. Nothing here signs, notarizes
or installs anything; `scripts/package.sh` is run only to see it refuse.
"""
from __future__ import annotations

import os
import plistlib
import re
import subprocess
import tempfile
import time
from pathlib import Path


def run(check) -> None:
    from app import importer, onboarding, product
    from app.appdata import AppData

    # -- one place for the offer ----------------------------------------------
    check("the offer is $29 once with a 14-day trial, set in app/product.py",
          (product.PRICE_USD, product.TRIAL_DAYS) == (29, 14))
    site = Path("site")
    index = (site / "index.html").read_text()
    check("the product site states the same price and trial",
          f"${product.PRICE_USD}" in index and f"{product.TRIAL_DAYS}-day" in index)
    check("and its Buy button is the checkout the app links to",
          f'href="{product.BUY_URL}"' in index)
    privacy = (site / "privacy.html").read_text().lower()
    check("the privacy policy says plainly that messages stay on the Mac",
          "never upload" in privacy and "other people" in privacy and "lemon squeezy" in privacy)
    for page in ("terms.html", "refund.html"):
        check(f"{page} exists and is marked as a template for legal review",
              "template" in (site / page).read_text().lower()
              and "legal review" in (site / page).read_text().lower())
    for page in site.glob("*.html"):
        html = page.read_text()
        check(f"site/{page.name} loads nothing from anywhere else",
              not re.search(r'<(script|link|img)[^>]+(src|href)="https?://', html))
    check("a release build is refused while the store ids and URLs are placeholders",
          product.release_blockers() and subprocess.run(
              [".venv/bin/python", "-m", "app.product", "--check-release"],
              capture_output=True).returncode == 1)

    # -- the bundle -----------------------------------------------------------
    import macapp.build as mb
    info = mb.info_plist(release=True)
    check("the app has one stable bundle id, so Full Disk Access is granted to it",
          info["CFBundleIdentifier"] == product.BUNDLE_ID == "com.messaginggraph.app")
    check("its version and minimum macOS come from app/product.py",
          info["CFBundleShortVersionString"] == product.VERSION
          and info["LSMinimumSystemVersion"] == product.MIN_MACOS)
    check("a release asks for no access to Desktop, Documents or Downloads",
          not any(k.endswith("FolderUsageDescription") for k in info))
    src = Path("macapp/build.py").read_text()
    check("a release has no link back to this project folder",
          'extra = [] if release else ["--add-data", f"{marker}:macapp"]' in src)
    ent = plistlib.loads(Path("macapp/entitlements.plist").read_bytes())
    check("the hardened runtime gets only the two exceptions it needs",
          set(ent) == {"com.apple.security.cs.allow-unsigned-executable-memory",
                       "com.apple.security.automation.apple-events"}, ", ".join(sorted(ent)))
    check("and nothing notarization refuses or that would weaken it further",
          not any(k in ent for k in ("com.apple.security.get-task-allow",
                                     "com.apple.security.cs.disable-library-validation",
                                     "com.apple.security.app-sandbox")))

    # -- packaging refuses without an identity ---------------------------------
    sh = Path("scripts/package.sh").read_text()
    env = {k: v for k, v in os.environ.items() if not k.startswith("MG_")}
    t = time.time()
    r = subprocess.run(["scripts/package.sh"], capture_output=True, text=True, env=env)
    check("scripts/package.sh refuses a release without MG_SIGN_IDENTITY and MG_NOTARY_PROFILE",
          r.returncode == 1 and "MG_SIGN_IDENTITY" in r.stderr and "MG_NOTARY_PROFILE" in r.stderr
          and time.time() - t < 10, r.stderr.strip().splitlines()[0] if r.stderr else "")
    r = subprocess.run(["scripts/package.sh", "--no-notarize"], capture_output=True, text=True,
                       env={**env, "MG_SIGN_IDENTITY": "Apple Development: Someone (X)"})
    check("and a certificate that isn't Developer ID", r.returncode == 1
          and "Developer ID Application" in r.stderr)
    r = subprocess.run(["scripts/package.sh", "--no-notarize"], capture_output=True, text=True,
                       env={**env, "MG_SIGN_IDENTITY": "Developer ID Application: Nobody (ZZZZZZZZZZ)"})
    check("and one that isn't in the keychain", r.returncode == 1 and "keychains" in r.stderr)
    check("it signs inside out with the hardened runtime and a secure timestamp",
          "--options runtime --timestamp" in sh and "--entitlements" in sh and "--deep --sign" not in sh)
    check("notarizes with a keychain profile, and staples the app and the DMG",
          "notarytool submit" in sh and '--keychain-profile "$MG_NOTARY_PROFILE"' in sh
          and 'stapler staple -q "$APP"' in sh and 'stapler staple -q "$DMG"' in sh)
    check("and treats anything but Accepted as a failure, with the way to see why",
          "== Accepted" in sh and "notarytool log" in sh)

    built = Path("dist") / f"{product.NAME}.app"
    if built.exists():
        d = subprocess.run(["codesign", "-dvvv", str(built)], capture_output=True, text=True)
        out = d.stdout + d.stderr
        check("the built app carries the hardened runtime and its bundle id",
              "runtime" in out and f"Identifier={product.BUNDLE_ID}" in out)
        check("the built app has no link back to a project folder",
              not list(built.rglob("project_path.txt")))

    # -- the first run --------------------------------------------------------
    data = AppData(Path(tempfile.mkdtemp()) / "app")
    check("a new Mac with nothing imported gets the onboarding screen",
          onboarding.needed(data, have_data=False))
    check("someone with data already imported doesn't", not onboarding.needed(data, have_data=True))
    onboarding.mark_done(data)
    check("and it doesn't come back once finished", not onboarding.needed(data, have_data=False))

    tmp = Path(tempfile.mkdtemp())
    readable = tmp / "chat.db"
    readable.write_bytes(b"SQLite format 3\x00" + b"\x00" * 64)
    locked = tmp / "locked.db"
    locked.write_bytes(b"x" * 32)
    locked.chmod(0o000)
    states = (onboarding.access_state(importer.access(str(readable))),
              onboarding.access_state(importer.access(str(locked))),
              onboarding.access_state(importer.access(str(tmp / "none.db"))))
    locked.chmod(0o600)
    check("it tells granted, not yet, and no Messages at all apart",
          states == (onboarding.GRANTED, onboarding.NOT_YET, onboarding.NO_MESSAGES), str(states))
    pages = {s: onboarding.page(s) for s in (onboarding.GRANTED, onboarding.NOT_YET,
                                              onboarding.NO_MESSAGES)}
    nope = pages[onboarding.NOT_YET]
    check("not yet: it says why, opens the right settings pane, and checks again",
          "Full Disk Access" in nope and "mg://settings" in nope and "mg://recheck" in nope
          and "mg://relaunch" in nope and "reads them during an import" in nope)
    check("the import button only works once access is granted",
          'href="mg://import"' in pages[onboarding.GRANTED]
          and 'href="mg://import"' not in nope)
    check("every state offers the demo instead", all("mg://demo" in p for p in pages.values()))
    check("it states the trial and the only two things that go online",
          f"${product.PRICE_USD}" in nope and "licence check" in nope and "update check" in nope)
    check("it says plainly that messages are never uploaded", "never uploaded" in nope)
    check("the settings link is the Full Disk Access pane",
          importer.SETTINGS_URL.endswith("Privacy_AllFiles"))
    dt = Path("app/desktop.py").read_text()
    check("the app shows it on first run and keeps checking while it is up",
          "onboarding.needed(" in dt and "_watch_access" in dt and "RECHECK_SECONDS" in dt)
    for link in ("recheck", "demo", "buy", "licence", "settings", "relaunch", "import"):
        check(f"and answers mg://{link} itself", f'what == "{link}"' in dt)
    check("after the trial the app's import shows the licence screen instead of reading",
          "import_allowed()" in dt and "licence_page" in dt)
    check("the in-process server (the one a customer runs) starts the licence and update checks",
          "server.start_background_checks()" in dt)
    lp = onboarding.licence_page("Your trial has ended.")
    check("the licence screen offers to buy, to enter a key, and to go back to your data",
          "mg://buy" in lp and "mg://licence" in lp and "mg://home" in lp
          and f"${product.PRICE_USD}" in lp)
