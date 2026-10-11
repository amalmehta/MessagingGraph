"""mg -- import, serve, check and wipe.

    ./mg import            read chat.db once (needs Full Disk Access), encrypt into the vault
    ./mg serve             serve the vault on 127.0.0.1 (no Full Disk Access needed)
    ./mg serve --synthetic serve the demo corpus instead
    ./mg doctor            check the machine and the vault for anything left open
    ./mg wipe              delete the vault and its Keychain key

Everything printed here is counts and settings. No names, numbers or message
text -- so output can be pasted anywhere, including a chat with an assistant.
"""
from __future__ import annotations

import argparse
import os
import stat
import subprocess
import sys

from .ingest.contacts import AB_ROOT
from .ingest.imessage import DB_PATH

OK, WARN, BAD = "  ok  ", "  !!  ", "  xx  "


def _line(mark: str, text: str) -> None:
    print(f"{mark}{text}")


HOST_APPS = {
    "com.apple.Terminal": "Terminal", "com.googlecode.iterm2": "iTerm",
    "com.microsoft.VSCode": "Visual Studio Code", "dev.warp.Warp-Stable": "Warp",
    "com.mitchellh.ghostty": "Ghostty", "net.kovidgoyal.kitty": "kitty",
    "com.github.wez.wezterm": "WezTerm", "org.alacritty": "Alacritty",
}


def _access_help() -> None:
    """Name the app macOS actually checks. Full Disk Access belongs to the app that
    hosts the shell, not the shell -- which is why "grant it to Terminal" doesn't help
    when the shell is running inside an editor, tmux, or the Claude app."""
    bundle = os.environ.get("__CFBundleIdentifier", "")
    name = HOST_APPS.get(bundle)
    if "anthropic" in bundle.lower() or "claude" in bundle.lower():
        _line(BAD, "This shell is running inside the Claude app, which doesn't have Full "
                   "Disk Access (and shouldn't).")
        print("      Run the import from the Terminal app instead.")
        return
    label = f"{name} ({bundle})" if name else (bundle or "the app running this shell")
    _line(BAD, "chat.db exists but this shell can't read it.")
    print(f"      macOS checks the app hosting this shell: {label}")
    print(f"      1. System Settings > Privacy & Security > Full Disk Access: "
          f"{name or 'that app'} must be listed AND switched on.")
    print(f"      2. Quit {name or 'it'} completely (⌘Q) and reopen. Closing the window "
          "isn't enough; macOS only rechecks on launch.")
    if os.environ.get("TMUX"):
        print("      3. You're inside tmux, whose server keeps the permissions it started "
              "with. Run this outside tmux.")


def cmd_import(a) -> int:
    from .ingest.imessage import IMessageSource
    from .vault import Vault, VaultError

    vault = Vault()
    try:
        id_key = vault.id_key()
    except VaultError as e:
        _line(BAD, str(e))
        return 1
    src = IMessageSource(path=a.chatdb, ab_root=a.addressbook, days=a.days, id_key=id_key)
    ok, why = src.available()
    if not ok:
        if os.path.exists(a.chatdb):
            _access_help()
        else:
            _line(BAD, why)
        return 1

    print(f"Reading a snapshot of chat.db (last {a.days} days)…")
    corpus = src.load()
    r = src.report
    if r.kept == 0:
        _line(BAD, "Nothing survived import, so nothing was written. Try a larger --days.")
        return 1

    texted = r.kept - r.attachment_only
    print(f"\n  rows read               {r.rows_read:>8,}")
    print(f"  messages kept           {r.kept:>8,}   ({r.attachment_only:,} attachment-only)")
    print(f"  text from attributedBody{r.from_attributed_body:>8,}")
    print(f"  tapbacks → reactions    {r.tapbacks_folded:>8,}   ({r.tapbacks_removed:,} removed)")
    for reason, n in sorted(r.dropped.items()):
        print(f"  dropped: {reason:<15}{n:>8,}")
    print(f"  people                  {len(corpus.contacts):>8,}   "
          f"({r.contacts_named:,} named, {r.contacts_unnamed:,} unnamed, "
          f"{r.handles_merged:,} handles merged)")
    print(f"  threads                 {r.threads_direct:>8,} direct, {r.threads_group:,} group\n")

    if r.unrecoverable_body and r.unrecoverable_body > 0.05 * max(1, texted):
        _line(WARN, f"{r.unrecoverable_body:,} message bodies couldn't be decoded. The "
                    "attributedBody format may have changed on this macOS version.")
    if r.contacts_named == 0:
        _line(WARN, "No names resolved from Contacts; people will show as phone numbers.")

    vault.write(corpus, {
        "window_days": a.days, "messages": r.kept, "people": len(corpus.contacts),
        "threads": len(corpus.threads),
    })
    size = vault.corpus_path.stat().st_size
    _line(OK, f"Encrypted to {vault.corpus_path} ({size / 1e6:.1f} MB)")
    _line(OK, "Key stored in the login Keychain; folder excluded from Spotlight and Time Machine")
    print("\nNext:\n  1. System Settings > Privacy & Security > Full Disk Access: switch your "
          "terminal back off.\n  2. ./mg serve")
    return 0



def cmd_serve(a) -> int:
    if a.synthetic:
        source = "synthetic"
    elif a.chatdb_source:
        source = f"chatdb:{a.chatdb_source}"
        os.environ["MG_AB_ROOT"] = a.addressbook
    else:
        from .vault import Vault
        if not Vault().exists():
            _line(BAD, "No imported data yet. Run ./mg import, or ./mg serve --synthetic.")
            return 1
        source = "vault"
    os.environ["MG_SOURCE"] = source
    os.environ["MG_PORT"] = str(a.port)

    host = "127.0.0.1"
    if a.phone:
        from .net import reachable_at
        where = reachable_at(a.port)
        if not where:
            _line(BAD, "This Mac isn't on a network I can find (no Wi-Fi or Ethernet address).")
            return 1
        os.environ["MG_PHONE"] = "1"
        host = "0.0.0.0"          # noqa: S104 -- deliberate, and only with --phone

    import uvicorn
    print(f"Loading {source.split(':')[0]} data…", flush=True)
    from . import main
    print(f"\nOpen this link (it works only for this run of the server):\n\n  "
          f"{main.guard.launch_url}\n", flush=True)
    if a.phone:
        _line(WARN, "Answering on your local network. Anyone on this Wi-Fi can reach "
                    "the pairing page; only a phone with a code gets past it.")
        print("      On the phone, open:  "
              f"http://{where[0]}/pair", flush=True)
        for extra in where[1:]:
            print(f"                     or:  http://{extra}/pair", flush=True)
        print("      Then open Privacy on this Mac and press Show a pairing code.\n", flush=True)
    uvicorn.run(main.app, host=host, port=a.port, access_log=False,
                server_header=False, log_level="warning")
    return 0


def _mode(path) -> str:
    return oct(stat.S_IMODE(os.stat(path).st_mode))


def cmd_doctor(a) -> int:
    from .vault import Vault
    problems = 0

    fv = subprocess.run(["fdesetup", "status"], capture_output=True, text=True).stdout.strip()
    if "On" in fv:
        _line(OK, "FileVault is on — data at rest is encrypted if this Mac is lost")
    else:
        _line(BAD, "FileVault is off. Turn it on in System Settings > Privacy & Security.")
        problems += 1

    try:
        with open(DB_PATH, "rb") as fh:
            fh.read(16)
        _line(WARN, "This terminal still has Full Disk Access. Switch it off unless "
                    "you're about to re-import — every program run from it can read everything.")
        problems += 1
    except PermissionError:
        _line(OK, "This terminal does not have Full Disk Access")
    except FileNotFoundError:
        _line(OK, "No Messages store on this machine")

    v = Vault()
    if not v.exists():
        _line(OK, "No vault yet (nothing imported)")
    else:
        m = v.manifest() or {}
        _line(OK, f"Vault from {m.get('imported_at', '?')}: {m.get('messages', '?'):,} messages, "
                  f"{m.get('people', '?')} people, last {m.get('window_days', '?')} days")
        dmode, fmode = _mode(v.root), _mode(v.corpus_path)
        good = dmode == "0o700" and fmode == "0o600"
        _line(OK if good else BAD, f"Permissions {dmode} (folder) / {fmode} (file)")
        problems += 0 if good else 1
        spot = (v.root / ".metadata_never_index").exists()
        _line(OK if spot else WARN, "Excluded from Spotlight" if spot else "Spotlight may index the vault folder")
        tm = subprocess.run(["tmutil", "isexcluded", str(v.root)],
                            capture_output=True, text=True).stdout
        excluded = "[Excluded]" in tm
        _line(OK if excluded else WARN,
              "Excluded from Time Machine" if excluded else "Not excluded from Time Machine")

    project = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    stray = [os.path.relpath(os.path.join(d, f), project)
             for d, _, fs in os.walk(project) if ".venv" not in d and "scratch" not in d
             for f in fs if f.endswith((".db", ".db-wal", ".enc", ".abcddb"))]
    if stray:
        _line(WARN, f"Message-store-shaped files inside the project folder: {', '.join(stray[:5])}")
        problems += 1
    else:
        _line(OK, "No message data inside the project folder")

    print(f"\n{'All clear.' if not problems else f'{problems} thing(s) to fix.'}")
    return 1 if problems else 0


def cmd_wipe(a) -> int:
    from .vault import Vault
    v = Vault()
    if not a.yes:
        reply = input(f"Delete {v.root} and its Keychain key? This can't be undone. [y/N] ")
        if reply.strip().lower() != "y":
            print("Nothing deleted.")
            return 1
    removed = v.wipe()
    _line(OK, f"Deleted {len(removed)} file(s) and the Keychain key" if removed
          else "Nothing on disk; Keychain key removed if it existed")
    return 0


def cmd_build_app(a) -> int:
    from macapp.build import main as build
    return build(login=not a.no_login)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="mg", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("import", help="read chat.db once into the encrypted vault")
    i.add_argument("--days", type=int, default=400, help="how far back to import (default 400)")
    i.add_argument("--chatdb", default=DB_PATH, help=argparse.SUPPRESS)
    i.add_argument("--addressbook", default=AB_ROOT, help=argparse.SUPPRESS)
    i.set_defaults(fn=cmd_import)


    s = sub.add_parser("serve", help="serve the app on 127.0.0.1")
    s.add_argument("--synthetic", action="store_true", help="serve the demo corpus")
    s.add_argument("--port", type=int, default=8077)
    s.add_argument("--phone", action="store_true",
                   help="also answer this Mac's own address, so a paired phone can read it")
    s.add_argument("--chatdb-source", default=None, help=argparse.SUPPRESS)
    s.add_argument("--addressbook", default=AB_ROOT, help=argparse.SUPPRESS)
    s.set_defaults(fn=cmd_serve)

    sub.add_parser("doctor", help="check for anything left open").set_defaults(fn=cmd_doctor)
    b = sub.add_parser("build-app", help="build and install the Mac app (needs: uv sync --group app)")
    b.add_argument("--no-login", action="store_true", help="don't start it at login")
    b.set_defaults(fn=cmd_build_app)
    w = sub.add_parser("wipe", help="delete the vault and its key")
    w.add_argument("--yes", action="store_true")
    w.set_defaults(fn=cmd_wipe)

    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
