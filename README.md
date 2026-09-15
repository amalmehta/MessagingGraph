# Messaging Graph

A private app for your Mac that reads your Messages history and helps you keep up with the
people in it: who you've drifted from, plans that never got a date, messages worth sending, and
how your friends connect to each other.

It runs entirely on your computer. Your messages are imported once into an encrypted file and are
never sent anywhere.

## What it does

- **People**: a profile for everyone you talk to, showing how often you talk, how warm it is,
  what the thread feels like, and whether it's picking up or going quiet.
- **Plans**: plans pulled out of your conversations, split into confirmed, still being
  discussed, and worth reviving. Click one to see the messages it came from.
- **Messages worth sending**: drafts in the way you usually text each person, for replying,
  following up, reconnecting or reviving a plan. You copy them; nothing sends itself.
- **Who's connected**: shared group chats, people who come up in other chats, shared
  interests, where people live, and who might get along.
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

Run `./mg import` again whenever you want fresher data. Your names, locations and other
corrections are kept.

## Other commands

| Command | What it does |
|---|---|
| `./mg doctor` | Checks for anything left open, like Full Disk Access still being on |
| `./mg wipe` | Deletes your imported data and its encryption key |

## Privacy

- Nothing leaves your Mac. The app makes no network requests.
- Imported messages are encrypted and stored in `~/Library/Application Support/messaging-graph`,
  with the key in your login Keychain, never inside the project folder.
- The app only responds to the browser that opens the one-time link it prints.

More detail in [docs/how-it-works.md](docs/how-it-works.md).

## Tests

```bash
.venv/bin/python validate.py
```

## Limitations

Version 1. macOS only, and the language analysis understands English only.
