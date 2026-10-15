# Instructions

[← Back to the README](../README.md) · [System design](SYSTEM-DESIGN.md) · [File structure](FILE-STRUCTURE.md)

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

## Requirements

- A Mac with Messages
- [uv](https://docs.astral.sh/uv/) (it installs the right Python for you)

## Setup

```bash
git clone <this repository>
cd messaging_graph
uv sync
```

## Try it with demo data

```bash
./mg serve --synthetic
```

Open the link it prints. The demo uses made-up people, so you can look around before connecting
your own messages.

## Use it with your own messages

### In the Mac app

Press **Import my messages**. macOS asks you to allow Messaging Graph to read them the first
time: the app opens the right settings pane, you switch it on, and press **Reopen**. Nothing is
read until you do.

Build the app with `./mg build-app` if you haven't.

### Or from a terminal

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
Disk Access rather than Terminal. It only reads `chat.db` during an import — serving your data
never touches it — and you can switch it back off in the same settings pane whenever you like.

## Mac app

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
Choose **Always Allow**: that's the key that decrypts your data. The app itself never needs
Full Disk Access; importing still happens in Terminal. Rebuild after pulling new changes.

## Other commands

| Command | What it does |
|---|---|
| `./mg doctor` | Checks for anything left open, like Full Disk Access still being on |
| `./mg wipe` | Deletes your imported data and its encryption key |

## Privacy

It runs entirely on your computer. Your messages are imported once into an encrypted file and are
never sent anywhere.

- Nothing leaves your Mac. The app makes no network requests.
- Imported messages are encrypted and stored in `~/Library/Application Support/messaging-graph`,
  with the key in your login Keychain, never inside the project folder.
- The app only responds to the browser that opens the one-time link it prints.

More detail in [How it works](how-it-works.md).

## Tests

```bash
.venv/bin/python validate.py
```

## Limitations

Version 1. macOS only, and the language analysis understands English only.
