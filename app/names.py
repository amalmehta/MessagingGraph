"""How to show a person when all you have is a number.

The first word of "(408) 555-0111" is its area code, so anything that takes a
"first name" shows two different people as "(408)". Every place that shortens a
name, titles a group, or says hello goes through here instead.
"""
from __future__ import annotations

import re

HANDLE = re.compile(r"^\(\d{3}\) \d{3}-\d{4}$|^\+?[\d\s().-]{7,}$|^[^\s@]+@[^\s@]+$")


def looks_like_handle(name: str) -> bool:
    return bool(HANDLE.match((name or "").strip()))


def short_name(name: str) -> str:
    """First name for people; last four digits (or the part before @) for numbers."""
    name = (name or "").strip()
    if not looks_like_handle(name):
        return name.split()[0] if name else ""
    if "@" in name:
        return name.split("@")[0]
    return "…" + re.sub(r"\D", "", name)[-4:]


def greeting_name(name: str) -> str | None:
    """A name to say hello with, or None when all you have is a number."""
    name = (name or "").strip()
    return None if not name or looks_like_handle(name) else name.split()[0]


def group_label(names: list[str]) -> str:
    """Title for a group chat that was never given a name."""
    return ", ".join(short_name(n) for n in names[:3]) + (f" +{len(names) - 3}" if len(names) > 3 else "")


def legacy_group_label(names: list[str]) -> str:
    """The format earlier imports baked in ("(408), (408)"), so it can be recognised and fixed."""
    return ", ".join(n.split()[0] for n in names[:3] if n.split()) + (
        f" +{len(names) - 3}" if len(names) > 3 else "")
