# Go-live

The steps only the owner can do: accounts, certificates, money and the legal pages. Everything else is
built. Back to the [README](../README.md) · [System design](SYSTEM-DESIGN.md) · [Instructions](INSTRUCTIONS.md)

Work through them in order; each says how to tell it worked.

The app is free for now (`FREE = True` in `app/product.py`), so step 4 (Lemon Squeezy), `BUY_URL` and the price
don't apply and a release build doesn't ask for them. They are kept for a paid Pro version later.

## 1. Settle the app's identity, once

macOS files Full Disk Access, the Keychain's trust and the login item under the bundle id, so changing it after
the first sale makes every customer grant Full Disk Access again.

- [ ] In [`app/product.py`](../app/product.py), keep or change `BUNDLE_ID` (`com.messaginggraph.app`). Use a
      reverse-DNS name you control.
- [ ] Set `COPYRIGHT` to your legal name.
- [ ] Check `PRICE_USD = 5` and `TRIAL_DAYS = 14`. If you change them, change `site/index.html` too:
      `validate.py` fails until the two agree.

## 2. Apple Developer Program and a Developer ID certificate

1. Enrol at [developer.apple.com/programs](https://developer.apple.com/programs/) ($99 a year). An individual
   enrolment shows your personal name as the developer; an organisation needs a D-U-N-S number.
2. In Xcode → Settings → Accounts, add your Apple Account, select the team, **Manage Certificates… → + →
   Developer ID Application**. (Or create it at developer.apple.com → Certificates with a CSR from Keychain
   Access.) Only the Account Holder can create Developer ID certificates.
3. Check it's in your login keychain with its private key:
   ```bash
   security find-identity -v -p codesigning
   #   1) ABC123…  "Developer ID Application: Your Name (TEAMID)"
   ```
4. Export the certificate and private key as a `.p12` and keep it somewhere safe and offline. Losing it means
   a new certificate; customers' Full Disk Access grants are tied to the Team ID, so they survive that, but
   not a change of team.

```bash
export MG_SIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)"
```

## 3. A notarization profile

1. At [account.apple.com](https://account.apple.com) → Sign-In and Security → App-Specific Passwords, make one
   called "notarytool".
2. Store it in your keychain under a profile name:
   ```bash
   xcrun notarytool store-credentials messaging-graph \
     --apple-id you@example.com --team-id TEAMID --password <app-specific password>
   ```
   (An App Store Connect API key works too: `--key`, `--key-id`, `--issuer`.)
3. Check: `xcrun notarytool history --keychain-profile messaging-graph` lists nothing, without an error.

```bash
export MG_NOTARY_PROFILE=messaging-graph
```

The script reads only these two names; the password and private key never leave your keychain.

## 4. The Lemon Squeezy product

1. Create a store at [lemonsqueezy.com](https://www.lemonsqueezy.com) and complete identity verification and
   payouts. Lemon Squeezy is merchant of record: it charges VAT and sales tax and pays you out (5% + $0.50 per
   sale on the standard plan; check current pricing).
2. **Products → New product**: "Messaging Graph", single payment, **$5**.
   - Turn on **Generate license keys**. Activation limit: **2** (the site and terms say "up to two Macs";
     change all three together). Expiry: never.
   - Add the DMG as the product's file, or link to your own download. The trial DMG and the paid DMG are the same
     file; the key is what's bought.
   - Confirmation email: say the key goes under **Privacy → Licence** in the app.
3. Copy the IDs into [`app/product.py`](../app/product.py):
   - `LS_STORE_ID`: Settings → Stores (the number in the store's URL or API).
   - `LS_PRODUCT_ID`: the product's ID (Products → ⋯ → Copy ID).
   - `BUY_URL`: the product's checkout link (Share → Checkout URL).
   Then put the same checkout link in both Buy buttons in `site/index.html`.
4. Refunds: in Settings → Refunds pick the window that matches `site/refund.html` (30 days in the template).
   Lemon Squeezy disables the key on refund; the app notices at its next weekly check.
5. Test in **test mode**: make a test purchase, then in a test build activate the test key, deactivate it, and
   refund it (expect "no longer active" at the next check, which `./mg licence check` forces). Test-mode keys
   only work while the product is in test mode.

The app uses only the public License API (`/v1/licenses/activate`, `validate`, `deactivate`). No Lemon Squeezy
API key goes in the app, and none should.

## 5. The site

`site/` is plain static HTML with no outside requests: host it anywhere that serves files over HTTPS
(Cloudflare Pages, Netlify, GitHub Pages, S3 + CloudFront).

1. Buy the domain. Set `SITE_URL` in `app/product.py` to it; `UPDATE_URL` follows as `SITE_URL/version.json`.
   Update the `url` in `site/version.json` to match.
2. Fill in every `[bracketed]` item in `privacy.html`, `terms.html` and `refund.html`, and **have them reviewed
   by a lawyer** for where you sell (EU/UK consumer withdrawal rights for digital content, GDPR/CCPA wording,
   governing law). They are templates.
3. If the web host keeps logs, say for how long in `privacy.html`. Don't add analytics: the privacy policy says
   there are none.
4. Put the DMG at `site/downloads/MessagingGraph-<version>.dmg` (or change the Download links to Lemon
   Squeezy's file link).
5. The landing page compares with "cloud personal CRMs" at "about $9–12 a month" without naming anyone. If you
   name competitors, check their current prices first and keep it factual.
6. Check: `python3 -m http.server -d site` and click through every link, then the same on the live domain.

After every release, update `site/version.json` to the new version; that is how existing customers hear about it.

## 6. Build, sign, notarize

On a Mac with the architecture you are selling for (see "Which Macs" below):

```bash
uv sync --group app
.venv/bin/python validate.py           # all checks pass
scripts/package.sh                     # refuses until steps 2-5 are done
```

It refuses, with the reason, if `MG_SIGN_IDENTITY` or `MG_NOTARY_PROFILE` is missing or wrong, or if
`app/product.py` still has placeholders. Otherwise it builds `dist/Messaging Graph.app`, signs every library
and then the app with the hardened runtime, notarizes and staples the app, makes and signs
`dist/MessagingGraph-<version>.dmg`, notarizes and staples that, and asks Gatekeeper about both. If Apple rejects
it, the script prints the `notarytool log` command that says why.

`scripts/package.sh --no-notarize` signs without sending to Apple (quicker, for checking the signature);
`--adhoc` makes a build for this Mac only.

**Which Macs.** A build matches the Mac it was built on. Build on Apple Silicon for Apple Silicon customers
(Intel Macs then run it under Rosetta, while macOS still ships it); if you want native Intel too, build again on
an Intel Mac and offer two downloads. The app says it needs macOS 13 or later (`MIN_MACOS`); test that floor
(step 7) or raise it.

## 7. Test on a clean Mac, as a customer

Use a Mac (or a macOS virtual machine, e.g. UTM on Apple Silicon) with a **fresh user account** that has never
run the app, has no `~/Library/Application Support/messaging-graph` and no "messaging-graph" Keychain item, and
is signed in to Messages with some history. Download the DMG **through a browser** so it is quarantined, as a
customer's would be.

- [ ] Opening the DMG and dragging to Applications shows no "unidentified developer" or "damaged" warning; the
      first open says only "downloaded from the Internet".
- [ ] `spctl -a -vv "/Applications/Messaging Graph.app"` → `accepted, source=Notarized Developer ID`.
- [ ] The first-run screen shows **Not yet**. *Open Full Disk Access settings* opens the right pane. Switch on
      Messaging Graph (add it with + if absent). The screen changes to **Allowed** by itself, or after *Reopen*.
- [ ] **Import my messages** finishes and shows your people. Contacts names appear (if not, macOS asked for
      Contacts access and was refused).
- [ ] Quit and reopen: no Keychain password prompt; the data is still there.
- [ ] Privacy → What goes online lists exactly the two calls. Privacy → Licence shows "Free trial: 14 days".
- [ ] Little Snitch or LuLu (or `nettop`) during a full session: no connections except, at most, the update
      check to your site.
- [ ] Activate a test-mode key: "Licensed". Move it (Privacy → Licence → Move to another Mac). Activate again.
- [ ] Trial end: set the date forward 15 days in System Settings (or edit `trial_started` in
      `~/Library/Application Support/Messaging Graph/app.json`), relaunch: everything still readable, import
      shows the licence screen, Buy opens the checkout.
- [ ] Turn Wi-Fi off and use it for a while: nothing breaks; a licensed copy stays licensed.
- [ ] Install a newer signed build over the old one: Full Disk Access is still granted, no new prompts.
- [ ] Menu bar → Check privacy in Terminal… asks once to control Terminal, then runs the check.
- [ ] Repeat the first-run test on the oldest macOS you claim (`MIN_MACOS`).

## 8. Launch

- [ ] Product published (out of test mode) in Lemon Squeezy, with the real DMG.
- [ ] `site/` deployed with the final legal pages and real Buy links; `version.json` current.
- [ ] A support email that you read, matching the one in the legal pages.
- [ ] Keep `uv.lock` and the signed DMG of every release; a customer on an old version may need it.

## Before each later release

Bump `VERSION` in `app/product.py` (and `pyproject.toml`), run `validate.py`, `scripts/package.sh`, test the
DMG on the clean account (at least: first run, import, licence), upload it to Lemon Squeezy and the site, then
update `site/version.json` last.
