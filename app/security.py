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
"""
from __future__ import annotations

import secrets

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse

COOKIE = "mg_session"

CSP = ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data:; connect-src 'self'; font-src 'self'; "
       "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")

# Served before there is a session, so it can't pull in styles.css. It follows the
# system light/dark the same way the app does, rather than always being dark.
LOCKED = """<!doctype html><meta charset="utf-8"><title>messaging graph</title>
<style>
:root{color-scheme:light dark;--page:#f6f5f2;--text:#4a4f55;--head:#1c1e21}
@media (prefers-color-scheme:dark){:root{--page:#191b1f;--text:#b3bac2;--head:#edeff2}}
body{font:15px system-ui;background:var(--page);color:var(--text);padding:48px}
h2{color:var(--head)}
</style>
<body>
<h2>Open the link printed in your terminal.</h2>
<p>This server only answers the browser that opens the one-time link it printed
when it started. Restarting the server prints a new one.</p></body>"""


def _eq(a: str | None, b: str) -> bool:
    return secrets.compare_digest((a or "").encode(), b.encode())


class Guard:
    def __init__(self, port: int) -> None:
        self.port = port
        self.hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        self.origins = {f"http://{h}" for h in self.hosts}
        self.token = secrets.token_urlsafe(32)
        self.session = secrets.token_urlsafe(32)

    @property
    def launch_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/?token={self.token}"

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
            if request.headers.get("host", "") not in self.hosts:
                return self.harden(PlainTextResponse("Blocked: unexpected Host header.", 403))
            if request.method not in ("GET", "HEAD") and \
                    request.headers.get("origin") not in self.origins:
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

            if not _eq(request.cookies.get(COOKIE), self.session):
                if path == "/":
                    return self.harden(HTMLResponse(LOCKED, 401))
                return self.harden(JSONResponse(
                    {"error": "Not signed in. Open the link printed in your terminal."}, 401))

            return self.harden(await call_next(request))
