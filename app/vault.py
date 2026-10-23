"""Encrypted at-rest store for an imported corpus.

The import runs once with Full Disk Access; this is where its result lives so
the server never needs that access again. Design, and what each part buys:

* **Location** -- ~/Library/Application Support/messaging-graph, outside the
  project folder, so a `git add .`, a zip of the project, or a synced Desktop can
  never pick it up. Directory 0700, files 0600.
* **Encryption** -- Fernet (AES-128-CBC with HMAC-SHA256) over a compressed
  serialization. Plaintext is never written, not even to a temp file.
* **Key** -- a random key in the login Keychain, not beside the data. A copy of
  the vault file on its own (a backup, a cloud upload, a stray `cp`) is noise.
* **Indexing and backups** -- excluded from Spotlight and Time Machine. It is a
  derived copy; `mg import` can always rebuild it from chat.db.

What it does not buy: protection from malware already running as you, which can
read the Keychain as easily as this app can. FileVault covers a lost laptop;
nothing at the application layer covers a compromised account.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import subprocess
import zlib
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Protocol

from cryptography.fernet import Fernet, InvalidToken

from .models import Contact, Corpus, Message, Thread
from .product import support_dir

VAULT_DIR = support_dir() / "messaging-graph"
CORPUS_FILE = "corpus.v1.enc"
MANIFEST_FILE = "manifest.json"
# Each platform is imported and kept on its own, because they are separate
# histories with separate people and the app shows them side by side rather
# than pretending they are one conversation.
PLATFORMS = ("imessage", "whatsapp", "instagram")
DEFAULT_PLATFORM = "imessage"
SERVICE = "messaging-graph"
# A build tried out under MG_HOME gets a Keychain item of its own, so it can
# never read -- or replace -- the key of the copy you actually use.
if os.environ.get("MG_HOME"):
    SERVICE += "-" + hashlib.sha256(os.environ["MG_HOME"].encode()).hexdigest()[:8]
ACCOUNT = "vault-key"
FORMAT = 1


class VaultError(RuntimeError):
    pass


class KeyStore(Protocol):
    def get(self) -> str | None: ...
    def set(self, value: str) -> None: ...
    def delete(self) -> None: ...


class KeychainKeyStore:
    """The macOS login Keychain, via the Security framework (no key on argv)."""

    def __init__(self) -> None:
        import keyring
        backend = keyring.get_keyring()
        if "macOS" not in type(backend).__module__:
            raise VaultError(
                f"Expected the macOS Keychain but keyring selected {type(backend).__name__}. "
                "Refusing to store the vault key anywhere weaker.")
        self._kr = keyring

    def get(self) -> str | None:
        return self._kr.get_password(SERVICE, ACCOUNT)

    def set(self, value: str) -> None:
        self._kr.set_password(SERVICE, ACCOUNT, value)

    def delete(self) -> None:
        try:
            self._kr.delete_password(SERVICE, ACCOUNT)
        except Exception:
            pass        # already gone, or the Keychain said no: nothing left to undo


class MemoryKeyStore:
    """For tests only: never touches the real Keychain."""

    def __init__(self, value: str | None = None) -> None:
        self.value = value

    def get(self) -> str | None:
        return self.value

    def set(self, value: str) -> None:
        self.value = value

    def delete(self) -> None:
        self.value = None


def _serialize(corpus: Corpus) -> bytes:
    doc = {
        "format": FORMAT,
        "source": corpus.source_name,
        "contacts": [asdict(c) for c in corpus.contacts.values()],
        "threads": [asdict(t) for t in corpus.threads.values()],
        "messages": [[m.id, m.thread_id, m.sender_id, m.ts.isoformat(), m.text,
                      m.is_from_me, list(m.reactions)] for m in corpus.messages],
    }
    return zlib.compress(json.dumps(doc, ensure_ascii=False,
                                    separators=(",", ":")).encode(), 6)


def _deserialize(blob: bytes) -> Corpus:
    doc = json.loads(zlib.decompress(blob))
    if doc.get("format") != FORMAT:
        raise VaultError(f"vault format {doc.get('format')} is not supported; re-run mg import")
    contacts = {c["id"]: Contact(id=c["id"], name=c["name"], handle=c["handle"],
                                 tags=tuple(c["tags"])) for c in doc["contacts"]}
    threads = {t["id"]: Thread(id=t["id"], name=t["name"],
                               participant_ids=tuple(t["participant_ids"]),
                               is_group=t["is_group"]) for t in doc["threads"]}
    messages = [Message(id=i, thread_id=tid, sender_id=s, ts=datetime.fromisoformat(ts),
                        text=tx, is_from_me=me, reactions=tuple(r))
                for i, tid, s, ts, tx, me, r in doc["messages"]]
    return Corpus(contacts=contacts, threads=threads, messages=messages,
                  source_name=doc["source"])


class Vault:
    def __init__(self, root: Path | str = VAULT_DIR, keys: KeyStore | None = None) -> None:
        self.root = Path(root)
        self._keys = keys

    @property
    def keys(self) -> KeyStore:
        if self._keys is None:
            self._keys = KeychainKeyStore()
        return self._keys

    @property
    def corpus_path(self) -> Path:
        return self.path_for(DEFAULT_PLATFORM)

    def path_for(self, platform: str = DEFAULT_PLATFORM) -> Path:
        """iMessage keeps the original name, so a vault imported before there
        were platforms is still the one the app reads."""
        if platform == DEFAULT_PLATFORM:
            return self.root / CORPUS_FILE
        return self.root / f"corpus.{platform}.v1.enc"

    def manifest_path(self, platform: str = DEFAULT_PLATFORM) -> Path:
        if platform == DEFAULT_PLATFORM:
            return self.root / MANIFEST_FILE
        return self.root / f"manifest.{platform}.json"

    def exists(self, platform: str = DEFAULT_PLATFORM) -> bool:
        return self.path_for(platform).exists()

    def imported(self) -> list[str]:
        """Which platforms have something to show."""
        return [p for p in PLATFORMS if self.exists(p)]

    def any(self) -> bool:
        """Whether anything at all has been imported. Asking `exists()` instead
        asks about iMessage, and someone who imported only WhatsApp -- the import
        that needs no permission -- was told they had no data."""
        return bool(self.imported())

    def source(self) -> str:
        """The source name to serve first: iMessage if it is there, otherwise
        the first platform that is."""
        have = self.imported()
        if not have or have[0] == DEFAULT_PLATFORM:
            return "vault"
        return f"vault:{have[0]}"

    def window_days(self, platform: str = DEFAULT_PLATFORM) -> int | None:
        """How far back this platform was imported, so a refresh can read the
        same span again rather than quietly shortening it."""
        try:
            d = json.loads(self.manifest_path(platform).read_text()).get("window_days")
        except (OSError, ValueError):
            return None
        return d if isinstance(d, int) and d > 0 else None

    def _key(self, create: bool) -> bytes:
        k = self.keys.get()
        if k is None:
            if not create:
                raise VaultError("No vault key in the Keychain. Run `./mg import` first.")
            k = Fernet.generate_key().decode()
            self.keys.set(k)
        return k.encode()

    def id_key(self) -> bytes:
        """Stable HMAC key for opaque ids, derived so re-imports keep the same ids
        (and so exclusions keep pointing at the same people)."""
        return hmac.new(self._key(create=True), b"messaging-graph/ids", hashlib.sha256).digest()

    def _prepare_dir(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        (self.root / ".metadata_never_index").touch(exist_ok=True)
        subprocess.run(["tmutil", "addexclusion", str(self.root)],
                       capture_output=True, check=False)

    def _write_private(self, name: str, data: bytes) -> None:
        tmp = self.root / f".{name}.tmp"
        if tmp.exists():
            tmp.unlink()
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.root / name)

    def write(self, corpus: Corpus, summary: dict,
              platform: str = DEFAULT_PLATFORM) -> None:
        self._prepare_dir()
        token = Fernet(self._key(create=True)).encrypt(_serialize(corpus))
        self._write_private(self.path_for(platform).name, token)
        manifest = {
            "format": FORMAT,
            "imported_at": datetime.now().isoformat(timespec="seconds"),
            "cipher": "Fernet (AES-128-CBC + HMAC-SHA256)",
            "key": f"login Keychain, service '{SERVICE}'",
            **summary,
        }
        manifest["platform"] = platform
        self._write_private(self.manifest_path(platform).name,
                            json.dumps(manifest, indent=2).encode())

    def read(self, platform: str = DEFAULT_PLATFORM) -> Corpus:
        if not self.exists(platform):
            raise VaultError(f"Nothing imported from {platform} yet.")
        try:
            blob = Fernet(self._key(create=False)).decrypt(self.path_for(platform).read_bytes())
        except InvalidToken as e:
            raise VaultError("The vault can't be decrypted with the key in the Keychain. "
                             "Run `./mg wipe` and import again.") from e
        return _deserialize(blob)

    def seal(self, name: str, obj: dict) -> None:
        """Encrypt a small JSON document into the vault folder with the vault key."""
        if not self.any():
            raise VaultError("No imported data yet. Run `./mg import` first.")
        token = Fernet(self._key(create=False)).encrypt(json.dumps(obj).encode())
        self._write_private(name, token)

    def unseal(self, name: str) -> dict | None:
        path = self.root / name
        if not path.exists():
            return None
        try:
            return json.loads(Fernet(self._key(create=False)).decrypt(path.read_bytes()))
        except InvalidToken as e:
            raise VaultError(f"{name} can't be decrypted with the key in the Keychain.") from e

    def manifest(self, platform: str | None = None) -> dict | None:
        """One platform's manifest; with none named, the one served first."""
        if platform is None:
            have = self.imported()
            platform = have[0] if have else DEFAULT_PLATFORM
        p = self.manifest_path(platform)
        return json.loads(p.read_text()) if p.exists() else None

    def wipe(self) -> list[str]:
        removed = []
        if self.root.exists():
            for f in sorted(self.root.iterdir()):
                f.unlink()
                removed.append(f.name)
            self.root.rmdir()
        self.keys.delete()
        return removed
