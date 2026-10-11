"""Hardening for a server that holds your messages on localhost.

"Binds to 127.0.0.1" is not a security boundary on its own. Every web page you
open can send requests to localhost, and three attacks follow from that:

* **DNS rebinding.** A hostile site resolves its own domain to 127.0.0.1 and then
  reads responses as same-origin. Stopped by refusing any Host header that isn't
  this server's own address.
* **Cross-site requests.** A page can fire a POST at /api/privacy to switch
  local-only off. Stopped by requiring a matching Origin on anything that changes
  state, and a SameSite=Strict session cookie, which browsers don't attach to
  requests from other sites.
* **Other local users and processes** poking the API. Stopped by a random launch
  token (as Jupyter does) printed only to the terminal that started the server,
  exchanged once for the session cookie.

On top of that, a strict Content-Security-Policy: message text is written by
whoever texted you, and group names by anyone in the group. The UI escapes all
of it; the CSP means a missed spot still can't run script or load anything from
elsewhere.

**When the phone is let in** (`./mg serve --phone`) the same shape holds, widened
by exactly as much as it has to be: the Host and Origin sets gain this Mac's own
LAN addresses and nothing else, and a second way in appears -- a device token
from `pairing.py`, carried in its own cookie. A phone that has it is trusted like
the browser on this Mac, with one exception: handing out pairing codes stays with
the session, so a paired phone cannot pair another one.
"""
from __future__ import annotations

import secrets

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse

COOKIE = "mg_session"
DEVICE_COOKIE = "mg_device"
DEVICE_MAX_AGE = 60 * 60 * 24 * 365        # a phone pairs once, not every week

CSP = ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data:; connect-src 'self'; font-src 'self'; manifest-src 'self'; "
       "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")

# Served before there is a session, so it can't pull in styles.css. It follows the
# system light/dark the same way the app does, rather than always being dark.
LOCKED = """<!doctype html><meta charset="utf-8"><title>messaging graph</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{color-scheme:light dark;--page:#f6f5f2;--text:#4a4f55;--head:#1c1e21}
@media (prefers-color-scheme:dark){:root{--page:#191b1f;--text:#b3bac2;--head:#edeff2}}
body{font:15px system-ui;background:var(--page);color:var(--text);padding:48px}
h2{color:var(--head)}
a{color:inherit}
</style>
<body>
<h2>Open the link printed in your terminal.</h2>
<p>This server only answers the browser that opens the one-time link it printed
when it started. Restarting the server prints a new one.</p>
%s</body>"""

# Only when the phone door is open, and built here rather than in script: the
# locked page is served to a browser that has shown nothing, and the less it is
# allowed to run there the better.
PAIR_HINT = """<p>On a phone? <a href="/pair">Pair it here</a> with the code this
Mac is showing under Privacy.</p>"""


def locked_page(on_lan: bool) -> str:
    return LOCKED % (PAIR_HINT if on_lan else "")


def _eq(a: str | None, b: str) -> bool:
    return secrets.compare_digest((a or "").encode(), b.encode())


class Guard:
    # The phone needs to reach these before it has anything to identify itself
    # with -- the pairing page, what it is built from, and the code exchange.
    OPEN_PATHS = {"/pair", "/static/pair.css", "/static/pair.js", "/api/pair/redeem"}

    def __init__(self, port: int, lan_hosts: tuple[str, ...] = (), device_for=None) -> None:
        self.port = port
        self.local = {f"127.0.0.1:{port}", f"localhost:{port}"}
        self.lan = {h.lower() for h in lan_hosts}
        self.hosts = self.local | self.lan
        self.origins = {f"http://{h}" for h in self.hosts}
        self.token = secrets.token_urlsafe(32)
        self.session = secrets.token_urlsafe(32)
        # Supplied by main.py, because the Guard shouldn't own who is paired --
        # it only needs to be able to ask.
        self.device_for = device_for or (lambda _t: None)

    @property
    def launch_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/?token={self.token}"

    @property
    def on_lan(self) -> bool:
        return bool(self.lan)

    @staticmethod
    def harden(resp):
        h = resp.headers
        h["content-security-policy"] = CSP
        h["x-content-type-options"] = "nosniff"
        h["referrer-policy"] = "no-referrer"
        h["x-frame-options"] = "DENY"
        h["cross-origin-opener-policy"] = "same-origin"
        h["cross-origin-resource-policy"] = "same-origin"
        h["cache-control"] = "no-store"
        return resp

    def install(self, app: FastAPI) -> None:
        @app.middleware("http")
        async def guard(request, call_next):
            if request.headers.get("host", "").lower() not in self.hosts:
                return self.harden(PlainTextResponse("Blocked: unexpected Host header.", 403))
            # Lowercased on both sides: `Amals-MacBook-Pro-2.local` is the name
            # scutil gives and the name a phone is told to type, and a host is
            # case-insensitive, so the two must not be allowed to disagree.
            if request.method not in ("GET", "HEAD") and \
                    request.headers.get("origin", "").lower() not in self.origins:
                return self.harden(PlainTextResponse("Blocked: cross-origin request.", 403))

            path = request.url.path
            supplied = request.query_params.get("token")
            if path == "/" and supplied is not None:
                if not _eq(supplied, self.token):
                    return self.harden(PlainTextResponse(
                        "That link isn't valid for this server. Use the one printed "
                        "when it started.", 401))
                resp = RedirectResponse("/", status_code=303)
                resp.set_cookie(COOKIE, self.session, httponly=True,
                                samesite="strict", path="/")
                return self.harden(resp)

            # Who is asking: this Mac's browser, a paired phone, or nobody yet.
            request.state.device = None
            request.state.via = None
            if _eq(request.cookies.get(COOKIE), self.session):
                request.state.via = "session"
            elif self.on_lan:
                device = self.device_for(request.cookies.get(DEVICE_COOKIE))
                if device is not None:
                    request.state.device, request.state.via = device, "device"

            # The pairing door only exists while the phone is let in; on loopback
            # alone these paths are as closed as everything else.
            if request.state.via is None and not (self.on_lan and path in self.OPEN_PATHS):
                if path == "/":
                    return self.harden(HTMLResponse(locked_page(self.on_lan), 401))
                return self.harden(JSONResponse(
                    {"error": "Not signed in. Open the link printed in your terminal."}, 401))

            return self.harden(await call_next(request))
