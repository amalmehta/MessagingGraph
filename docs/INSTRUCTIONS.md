# Instructions

[← Back to the README](../README.md) · [System design](SYSTEM-DESIGN.md) · [File structure](FILE-STRUCTURE.md) · [Go-live](GO-LIVE.md)

## What it does

- **People**: a profile for everyone you talk to, showing how often you talk, how warm it is,
  what the thread feels like, and whether it's picking up or going quiet.
- **Plans**: plans pulled out of your conversations, split into confirmed, still being
  discussed, and worth reviving. Click one to see the messages it came from.
- **Messages worth sending**: drafts in the way you usually text each person, for replying,
  following up, reconnecting or reviving a plan. You copy them; nothing sends itself.
- **Say thanks**: thank-you notes tied to something that actually happened, like a favour you
  thanked someone for, a year of texting, or something you did together.
- **Get a crew together**: say what the plan is — a volo league, a ski trip, a game night —
  and it picks who to ask, based on who talks about that thing, who's actually done it with
  you, who shows up, and who already knows each other. It drafts the group invitation too.
- **Who's connected**: shared group chats, people who come up in other chats, and shared
  interests.
- **Clean data**: marketing and automated threads are set aside, and you can name unknown
  numbers and add locations.

## Install the app

1. Open `MessagingGraph-<version>.dmg` and drag **Messaging Graph** to Applications. It carries its own
   Python; nothing else needs installing.
2. Open it. The first screen explains what it needs: macOS keeps Messages behind **Full Disk Access**, so press
   **Open Full Disk Access settings**, switch on **Messaging Graph** (press **+** and pick it from Applications
   if it isn't listed), and come back. The screen notices by itself; if it still says "Not yet", press
   **Reopen**.
3. Press **Import my messages**, or **Look around with made-up people first**.

Needs macOS 13 or later.

## Trial and licence

The first 14 days are a free trial with everything working. After that it's $5, once. When the trial ends,
everything you imported stays readable; only importing new messages waits for a licence.

- Buy from the app (**Privacy → Licence → Buy**) or the website. The key arrives by email.
- Paste it under **Privacy → Licence** and press **Activate**. Or: `./mg licence activate YOUR-KEY`.
- Moving to another Mac: **Move to another Mac** on the old one, then activate on the new one. A key works on
  two Macs at a time.
- A licensed copy checks its key about once a week. Offline is fine: it keeps working for 30 days after the
  last check that got through.

## Run it from source

### Requirements

- A Mac with Messages
- [uv](https://docs.astral.sh/uv/) (it installs the right Python for you)

### Setup

```bash
git clone <this repository>
cd messaging_graph
uv sync
```

### Try it with demo data

```bash
./mg serve --synthetic
```

Open the link it prints. The demo uses made-up people, so you can look around before connecting
your own messages.

### Use it with your own messages

#### In the Mac app

Press **Import my messages**. macOS asks you to allow Messaging Graph to read them the first
time: the app opens the right settings pane, you switch it on, and press **Reopen**. Nothing is
read until you do.

Build the app with `./mg build-app` if you haven't.

#### Or from a terminal

1. Open **System Settings → Privacy & Security → Full Disk Access**, turn it on for **Terminal**,
   then quit and reopen Terminal.
2. Import your messages:
   ```bash
   ./mg import
   ```
3. Turn Full Disk Access back off for Terminal.
4. Start the app and open the link it prints:
   ```bash
   ./mg serve
   ```

Import again whenever you want fresher data. Your names, locations and other corrections are
kept either way.

**Which app holds the permission.** Importing in the Mac app means Messaging Graph holds Full
Disk Access rather than Terminal. macOS ties the grant to the app's bundle id and signature, so it carries
over to updates signed by the same developer. It only reads `chat.db` during an import — serving your data
never touches it — and you can switch it back off in the same settings pane whenever you like.

### Build the Mac app yourself

Prefer an app to Terminal? Build and install it once:

```bash
uv sync --group app
./mg build-app
```

This puts **Messaging Graph** in your Applications folder and starts it at login (add
`--no-login` to skip that). It opens in its own window; closing the window keeps it in the
menu bar, where you can reopen it, reload your data, run an import or privacy check in
Terminal, turn start-at-login on or off, and quit.

The first time it opens, macOS asks whether it can use the "messaging-graph" Keychain item.
Choose **Always Allow**: that's the key that decrypts your data. Rebuild after pulling new changes.

To build the version that's sold (signed, notarized, in a DMG) use `scripts/package.sh`; see
[Go-live](GO-LIVE.md). `scripts/package.sh --adhoc` makes a build signed for this Mac only, to try on
this Mac. To try a build without touching the copy you use, point it at a scratch folder:

```bash
MG_HOME=/tmp/mg-try "dist/Messaging Graph.app/Contents/MacOS/Messaging Graph"
```

## Other commands

| Command | What it does |
|---|---|
| `./mg doctor` | Checks for anything left open, like Full Disk Access still being on |
| `./mg wipe` | Deletes your imported data and its encryption key (not your licence) |
| `./mg licence` | Shows the trial or licence; `activate KEY`, `deactivate`, `check` |

## Privacy

It runs entirely on your computer. Your messages are imported once into an encrypted file and are
never sent anywhere.

- Your messages never leave your Mac. The app makes two kinds of network request, and neither contains
  anything from your messages: the licence check (to Lemon Squeezy, only once you've bought a key: the key,
  an activation id and a random name) and a weekly update check (a plain request for one file from the
  product site, which you can switch off under Privacy). Both are listed, with an audit log, under Privacy.
- Imported messages are encrypted and stored in `~/Library/Application Support/messaging-graph`,
  with the key in your login Keychain, never inside the project folder.
- The app only responds to the browser that opens the one-time link it prints.

More detail in [How it works](how-it-works.md).

## Tests

```bash
uv sync --group app          # the Mac app's checks import PyObjC
.venv/bin/python validate.py
```

It takes about three minutes, writes only to a scratch folder, and makes no network calls.

## Limitations

Version 1.1. macOS 13 or later, and the language analysis understands English only. Apple doesn't
document the Messages database, so a macOS update can stop imports until the app is updated; what you
already imported keeps working.
