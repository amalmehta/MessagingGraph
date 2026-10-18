"""The ingestion seam."""
from __future__ import annotations

from ..models import Corpus


class SourceUnavailable(RuntimeError):
    """The underlying store exists but cannot be read (usually permissions)."""


def load_source(name: str) -> Corpus:
    """`synthetic`, `vault` (the encrypted import), or `chatdb:<path>` for a
    specific chat.db copy -- used by tests and fixtures, never the default."""
    if name == "synthetic":
        from .synthetic import SyntheticSource
        return SyntheticSource().load()
    if name == "vault":
        from ..vault import Vault
        return Vault().read()
    if name.startswith("vault:"):
        # One platform's history. They are kept and shown apart, because they
        # are different people saying different things in different places.
        from ..vault import Vault
        return Vault().read(name.split(":", 1)[1])
    if name == "whatsapp":
        from .whatsapp import WhatsAppSource
        return WhatsAppSource().load()
    if name.startswith("chatdb:"):
        import os
        from .contacts import AB_ROOT
        from .imessage import IMessageSource
        return IMessageSource(path=name.split(":", 1)[1],
                              ab_root=os.environ.get("MG_AB_ROOT", AB_ROOT)).load()
    raise ValueError(f"unknown source: {name!r}")
