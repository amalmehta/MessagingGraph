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

VAULT_DIR = Path.home() / "Library" / "Application Support" / "messaging-graph"
CORPUS_FILE = "corpus.v1.enc"
MANIFEST_FILE = "manifest.json"
SERVICE = "messaging-graph"
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
            pass


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
        return self.root / CORPUS_FILE

    def exists(self) -> bool:
        return self.corpus_path.exists()

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

    def write(self, corpus: Corpus, summary: dict) -> None:
        self._prepare_dir()
        token = Fernet(self._key(create=True)).encrypt(_serialize(corpus))
        self._write_private(CORPUS_FILE, token)
        manifest = {
            "format": FORMAT,
            "imported_at": datetime.now().isoformat(timespec="seconds"),
            "cipher": "Fernet (AES-128-CBC + HMAC-SHA256)",
            "key": f"login Keychain, service '{SERVICE}'",
            **summary,
        }
        self._write_private(MANIFEST_FILE, json.dumps(manifest, indent=2).encode())

    def read(self) -> Corpus:
        if not self.exists():
            raise VaultError("No imported data yet. Run `./mg import` first.")
        try:
            blob = Fernet(self._key(create=False)).decrypt(self.corpus_path.read_bytes())
        except InvalidToken as e:
            raise VaultError("The vault can't be decrypted with the key in the Keychain. "
                             "Run `./mg wipe` and import again.") from e
        return _deserialize(blob)

    def seal(self, name: str, obj: dict) -> None:
        """Encrypt a small JSON document into the vault folder with the vault key."""
        if not self.exists():
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

    def manifest(self) -> dict | None:
        p = self.root / MANIFEST_FILE
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
