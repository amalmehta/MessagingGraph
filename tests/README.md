# Tests

[← Back to the README](../README.md) · [File structure](../docs/FILE-STRUCTURE.md)

Everything here runs from one command, which also checks the analysis against the demo
generator's hidden parameters:

```bash
.venv/bin/python validate.py
```

| File | What it checks |
|---|---|
| [`chatdb_fixture.py`](chatdb_fixture.py) | A small `chat.db` and AddressBook built the way macOS writes them: WAL-only rows, `attributedBody`, tapbacks, hostile names. Also the same store as older macOS versions shaped it |
| [`ingest_checks.py`](ingest_checks.py) | Reading that store as each macOS version shaped it |
| [`e2e_fixture.py`](e2e_fixture.py) | A whole fake history: a `chat.db`, its AddressBook, WhatsApp Desktop's `ChatStorage.sqlite`, and the same WhatsApp chats as two Export chat zips |
| [`e2e_checks.py`](e2e_checks.py) | That history imported, served and read back over the HTTP API, compared with [`snapshots/e2e.json`](snapshots/e2e.json) |
| [`whatsapp_export_checks.py`](whatsapp_export_checks.py) | Reading Export chat files: dates that read two ways, 12-hour clocks, telling which name is yours, files that aren't exports |
| [`digest_checks.py`](digest_checks.py) | The weekly digest: which few things it picks, that the set turns over each Monday, snoozes running out exactly when they say, what an empty week says, and when the Mac app's notification goes out |
| [`licence_checks.py`](licence_checks.py) | The trial, licence keys, and the only two calls that go online |
| [`shipping_checks.py`](shipping_checks.py) | The app bundle, the first run, and the product site |

## The end-to-end check

`e2e_checks.py` runs the real `mg import` on the fixture: chat.db first, then WhatsApp
beside it. The vault goes in a temporary folder, and its key goes in `MemoryKeyStore`, so
the real Keychain and your own vault are never touched. The app then serves that vault and
is asked, over its own API, for:

- **people**: each person's state, trend, message count and rates
- **drift**: who has dropped furthest below their usual rate, worst first
- **plans**: every plan with its state, and how many are still open
- **redaction**: what a draft for Léa, Yuki or Aarav would send, with their numbers and
  IBANs hidden
- **search**: the hits for `cafe`, `café`, `встреча`, `東京` and `climbing`
- **group credit**: who wrote how much in each group chat

It does this four times: for iMessage, for WhatsApp in the same vault, for a second vault
that holds only WhatsApp, and for a third filled from Export chat zips with
`mg import --whatsapp-export`.

The fixture holds the cases that recent fixes were for:

| Case | Where it is in the fixture |
|---|---|
| Group messages credited to whoever wrote them | "Climbing crew" (iMessage), and "Berlin flatmates" (WhatsApp), where one row has no member and must be dropped |
| Phone numbers from outside the US, and IBANs | Léa (+33, a French IBAN), Yuki (+81), Aarav (+91, a German IBAN) |
| café and cafe | Priya, Léa and Sofía each mention a café |
| Scripts other than Latin | Дмитрий writes in Russian, Yuki in Japanese |
| Stale plans | A coffee with Marcus nobody followed up on (stale), and a dinner with Дмитрий raised again yesterday (no longer stale) |
| A friend's yes confirms a group plan | Climbing on Saturday, and dinner on Friday |
| A WhatsApp-only import | The second vault |
| WhatsApp from Export chat | Aarav's chat as an iPhone exports it (day-first, 24-hour, a photo, a wrapped message, an edit, a deletion), and the flatmates' group as an American Android phone does (month-first, 12-hour, notices with no sender, Jonas known only by his number) |

Every name, number and IBAN in it is made up. The phone numbers are in fictional or unused
ranges, and the IBANs are the standard published examples.

The fixture is built fresh on every run instead of being committed, because `.gitignore`
keeps `*.db` files out of the repository. Every date in it is counted from a fixed
`ANCHOR`, not from today, so the snapshot comes out the same whatever day the check runs.
To look at the files themselves:

```bash
.venv/bin/python -m tests.e2e_fixture /tmp/mg-e2e-fixture
```

### Running it on its own

```bash
.venv/bin/python -m tests.e2e_checks
```

The named checks run first, one for each fix, so a failure says which fix broke. After
them, each platform's output is compared with the snapshot as a whole. When that
comparison fails, it prints a diff of the snapshot against the current output.

### Regenerating the snapshot

When a change to the output is one you meant (a better drift ranking, a new plan state, a
search that ranks differently), write the snapshot again:

```bash
.venv/bin/python -m tests.e2e_checks --update
```

Then check what changed before you commit it:

```bash
git diff tests/snapshots/e2e.json
```

Read the diff the way a reviewer would. Every line in it should be something you meant to
change. The `--update` run still runs the named checks, and it fails if one of those
fails. Rewriting the snapshot cannot hide a broken fix. If the snapshot file is missing,
a plain run writes a new one.

If you change `e2e_fixture.py` itself, regenerate the snapshot in the same commit, so the
fixture and its expected output always match.
