# File structure

[← Back to the README](../README.md) · [Instructions](INSTRUCTIONS.md) · [System design](SYSTEM-DESIGN.md)

| Path | What it is |
|---|---|
| [`mg`](../mg) | Command-line entry point: `./mg import`, `serve`, `doctor`, `wipe`, `build-app` |
| [`run.sh`](../run.sh) | Starts the app with demo data (`./mg serve --synthetic`) |
| [`app/cli.py`](../app/cli.py) | The commands behind `./mg` |
| [`app/main.py`](../app/main.py) | Local HTTP API, bound to 127.0.0.1 |
| [`app/desktop.py`](../app/desktop.py) | The Mac app: its own window, a menu bar item, start at login |
| [`app/ingest/`](../app/ingest) | Readers for Messages (`chat.db`), Contacts, and the synthetic demo corpus |
| [`app/analysis/`](../app/analysis) | Relationships, plans, drafts, thank-yous, crews, connections, places, vibes, voice and spam filtering |
| [`app/graph.py`](../app/graph.py) | The plan graph: people and live plans as nodes |
| [`app/models.py`](../app/models.py) | Core data types shared by ingestion, analysis and the API |
| [`app/vault.py`](../app/vault.py) | Encrypted at-rest store for an imported corpus |
| [`app/privacy.py`](../app/privacy.py), [`app/security.py`](../app/security.py) | Privacy controls and localhost hardening |
| [`app/overrides.py`](../app/overrides.py), [`app/names.py`](../app/names.py) | Your own corrections, and how to show a person with only a number |
| [`app/state.py`](../app/state.py) | Process-wide state |
| [`app/static/`](../app/static) | The web UI (HTML, CSS, JavaScript) |
| [`macapp/`](../macapp) | Builds and installs `Messaging Graph.app` |
| [`tests/`](../tests) | Test fixtures, including a small `chat.db` shaped like the real one |
| [`validate.py`](../validate.py) | Checks the analysis against the demo generator's hidden parameters |
| [`docs/`](.) | These docs and [How it works](how-it-works.md) |
| [`docs/SYSTEM-DESIGN.md`](SYSTEM-DESIGN.md) | Components, main flows, storage and design decisions |
| [`pyproject.toml`](../pyproject.toml), [`uv.lock`](../uv.lock) | Python project and locked dependencies |
