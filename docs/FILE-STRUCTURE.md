# File structure

[← Back to the README](../README.md) · [Instructions](INSTRUCTIONS.md) · [System design](SYSTEM-DESIGN.md) · [Go-live](GO-LIVE.md)

| Path | What it is |
|---|---|
| [`mg`](../mg) | Command-line entry point: `./mg import`, `serve`, `doctor`, `wipe`, `licence`, `build-app` |
| [`run.sh`](../run.sh) | Starts the app with demo data (`./mg serve --synthetic`) |
| [`app/cli.py`](../app/cli.py) | The commands behind `./mg` |
| [`app/main.py`](../app/main.py) | Local HTTP API, bound to 127.0.0.1 |
| [`app/desktop.py`](../app/desktop.py) | The Mac app: its own window, a menu bar item, start at login |
| [`app/onboarding.py`](../app/onboarding.py) | The first-run screen: Full Disk Access, why, the Settings link, re-checking; the licence screen |
| [`app/importer.py`](../app/importer.py) | Imports from inside the app, after asking the licence and macOS whether it may |
| [`app/product.py`](../app/product.py) | The one place for price, trial, version, bundle id, Lemon Squeezy ids and site URLs |
| [`app/licence.py`](../app/licence.py) | Trial, licence keys against Lemon Squeezy's License API, offline grace |
| [`app/updates.py`](../app/updates.py) | The weekly "is there a newer version?" check |
| [`app/appdata.py`](../app/appdata.py) | The app's own small file (licence, trial, onboarding), kept apart from the vault |
| [`app/ingest/`](../app/ingest) | Readers for Messages (`chat.db`), Contacts, and the synthetic demo corpus |
| [`app/analysis/`](../app/analysis) | Relationships, plans, drafts, thank-yous, crews, connections, places, vibes, voice and spam filtering |
| [`app/graph.py`](../app/graph.py) | The plan graph: people and live plans as nodes |
| [`app/models.py`](../app/models.py) | Core data types shared by ingestion, analysis and the API |
| [`app/vault.py`](../app/vault.py) | Encrypted at-rest store for an imported corpus |
| [`app/privacy.py`](../app/privacy.py), [`app/security.py`](../app/security.py) | Privacy controls, the network gate (including the licence and update calls), and localhost hardening |
| [`app/overrides.py`](../app/overrides.py), [`app/names.py`](../app/names.py) | Your own corrections, and how to show a person with only a number |
| [`app/state.py`](../app/state.py) | Process-wide state |
| [`app/static/`](../app/static) | The web UI (HTML, CSS, JavaScript) |
| [`macapp/`](../macapp) | Builds `Messaging Graph.app` (`build.py`), and its hardened-runtime `entitlements.plist` |
| [`scripts/package.sh`](../scripts/package.sh) | The release build: the `.app` and DMG, Developer ID–signed, notarized and stapled (`--adhoc` for a local test) |
| [`site/`](../site) | The product site: landing page with price and Buy button, privacy policy, terms and refund templates, `version.json` |
| [`tests/`](../tests) | A small `chat.db` shaped like the real one and its older-macOS variants; ingest, licence and shipping checks run by `validate.py` |
| [`validate.py`](../validate.py) | Checks the analysis against the demo generator's hidden parameters |
| [`docs/`](.) | These docs and [How it works](how-it-works.md) |
| [`docs/SYSTEM-DESIGN.md`](SYSTEM-DESIGN.md) | Components, main flows, storage, design decisions and the Messages schema across macOS versions |
| [`docs/GO-LIVE.md`](GO-LIVE.md) | What only the owner can do before selling: Apple, notarization, Lemon Squeezy, the site, clean-Mac testing |
| [`pyproject.toml`](../pyproject.toml), [`uv.lock`](../uv.lock) | Python project and locked dependencies |
