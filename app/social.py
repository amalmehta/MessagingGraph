"""Where an imported social graph lives.

Sealed into the same vault, with the same Keychain key, as the messages. For the
demo and the tests it lives in memory and is thrown away with the process.
"""
from __future__ import annotations

from datetime import datetime

from .models import SocialAccount, SocialGraph

SOCIAL_FILE = "social.v1.enc"


def to_dict(graph: SocialGraph) -> dict:
    return {
        "network": graph.network,
        "exported_at": graph.exported_at.isoformat() if graph.exported_at else None,
        "accounts": [
            {"handle": a.handle, "name": a.name, "follows_me": a.follows_me,
             "i_follow": a.i_follow, "close_friend": a.close_friend, "blocked": a.blocked,
             "since": a.since.isoformat() if a.since else None}
            for a in graph.accounts.values()
        ],
    }


def from_dict(d: dict) -> SocialGraph:
    def when(v):
        return datetime.fromisoformat(v) if v else None
    accounts = {}
    for a in d.get("accounts", []):
        acc = SocialAccount(handle=a["handle"], name=a.get("name") or a["handle"],
                            follows_me=bool(a.get("follows_me")), i_follow=bool(a.get("i_follow")),
                            close_friend=bool(a.get("close_friend")), blocked=bool(a.get("blocked")),
                            since=when(a.get("since")))
        accounts[acc.handle] = acc
    return SocialGraph(network=d.get("network", "instagram"), accounts=accounts,
                       exported_at=when(d.get("exported_at")))


class SocialStore:
    def __init__(self, vault=None) -> None:
        self.vault = vault
        self._memory: SocialGraph | None = None

    def load(self) -> SocialGraph | None:
        if self.vault is None:
            return self._memory
        data = self.vault.unseal(SOCIAL_FILE)
        return from_dict(data) if data else None

    def save(self, graph: SocialGraph) -> None:
        if self.vault is None:
            self._memory = graph
        else:
            self.vault.seal(SOCIAL_FILE, to_dict(graph))

    def clear(self) -> None:
        if self.vault is None:
            self._memory = None
        else:
            self.vault.seal(SOCIAL_FILE, {"accounts": []})
