"""The trial, the licence and the two calls that leave the Mac.

Run from validate.py, which passes in its `check`. Every network call here goes to
a fake HTTP client that answers the way Lemon Squeezy's License API does
(https://docs.lemonsqueezy.com/api/license-api), so nothing reaches the internet
and no real key is ever used.
"""
from __future__ import annotations

import tempfile
from datetime import datetime, timedelta
from pathlib import Path

STORE, PRODUCT = 4242, 777


class FakeLemonSqueezy:
    """Answers activate, validate and deactivate the way the License API does,
    or not at all (`offline`), and remembers exactly what it was sent."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []
        self.offline = False
        self.keys = {"MG-GOOD-KEY-0001": {"status": "active", "store": STORE, "product": PRODUCT},
                     "OTHER-PRODUCT-KEY": {"status": "active", "store": STORE, "product": 1}}
        self.instances: dict[str, str] = {}
        self.server_error = False
        self.site = {"version": "1.2.0", "url": "https://site.test/download"}

    def _meta(self, k: dict) -> dict:
        return {"store_id": k["store"], "product_id": k["product"], "variant_id": 1,
                "customer_email": "buyer@example.com", "customer_name": "A Buyer"}

    def post(self, url: str, fields: dict, timeout: float):
        from app.licence import Response, TransportError
        self.calls.append(("POST", url, dict(fields)))
        if self.offline:
            raise TransportError("offline")
        if self.server_error:
            return Response(503, {})
        action = url.rsplit("/", 1)[-1]
        k = self.keys.get(fields.get("license_key", ""))
        if k is None:
            word = {"activate": "activated", "validate": "valid", "deactivate": "deactivated"}[action]
            return Response(404, {word: False, "error": "license_key not found."})
        lk = {"id": 1, "status": k["status"], "key": fields["license_key"],
              "activation_limit": 2, "activation_usage": len(self.instances)}
        if action == "activate":
            iid = f"inst-{len(self.instances) + 1}"
            self.instances[iid] = fields["license_key"]
            return Response(200, {"activated": True, "error": None, "license_key": lk,
                                  "instance": {"id": iid, "name": fields["instance_name"]},
                                  "meta": self._meta(k)})
        iid = fields.get("instance_id", "")
        if iid not in self.instances:
            return Response(404, {"valid": False, "deactivated": False,
                                  "error": "license_key instance not found."})
        if action == "validate":
            ok = k["status"] == "active"
            return Response(200, {"valid": ok, "error": None if ok else "expired",
                                  "license_key": lk, "instance": {"id": iid},
                                  "meta": self._meta(k)})
        self.instances.pop(iid)
        return Response(200, {"deactivated": True, "error": None, "license_key": lk,
                              "meta": self._meta(k)})

    def get(self, url: str, timeout: float):
        from app.licence import Response, TransportError
        self.calls.append(("GET", url, {}))
        if self.offline:
            raise TransportError("offline")
        return Response(200, dict(self.site))


def run(check) -> None:
    from app import licence as lic_mod, product, updates
    from app.appdata import AppData
    from app.licence import Licence, LicenceError, import_allowed
    from app.privacy import SERVICES, ConsentError, NetworkGate, PrivacySettings, ServiceRule

    was_ids = (product.LS_STORE_ID, product.LS_PRODUCT_ID)
    product.LS_STORE_ID, product.LS_PRODUCT_ID = STORE, PRODUCT
    try:
        _run(check, lic_mod, product, updates, AppData, Licence, LicenceError, import_allowed,
             SERVICES, ConsentError, NetworkGate, PrivacySettings, ServiceRule)
    finally:
        product.LS_STORE_ID, product.LS_PRODUCT_ID = was_ids


def _run(check, lic_mod, product, updates, AppData, Licence, LicenceError, import_allowed,
         SERVICES, ConsentError, NetworkGate, PrivacySettings, ServiceRule) -> None:
    root = Path(tempfile.mkdtemp(prefix="mg-licence-"))
    clock = [datetime(2026, 3, 1, 9, 0)]
    now = lambda: clock[0]                               # noqa: E731
    ls = FakeLemonSqueezy()
    gate = NetworkGate(PrivacySettings())
    data = AppData(root / "app")
    L = Licence(data, transport=ls, gate=gate, now=now)

    # -- the trial ----------------------------------------------------------
    st = L.status()
    check("a first launch starts a full trial, with nothing to buy yet",
          st.state == "trial" and st.can_import and st.trial_days_left == product.TRIAL_DAYS,
          st.message)
    check("and the trial makes no network call at all", ls.calls == [] and gate.audit == [])
    check("the trial is remembered rather than restarting each launch",
          Licence(data, transport=ls, now=now).status().trial_ends == st.trial_ends)
    check("the licence file is private and kept apart from the vault",
          oct(data.path.stat().st_mode & 0o777) == "0o600"
          and "messaging-graph" not in str(lic_mod.AppData().root).split("/")[-1])
    clock[0] += timedelta(days=product.TRIAL_DAYS - 1)
    check("the last day of the trial still counts down honestly",
          L.status().trial_days_left == 1 and "1 day left" in L.status().message)
    clock[0] += timedelta(days=1, seconds=1)
    st = L.status()
    check("after the trial, imports stop and what you imported stays readable",
          st.state == "trial_over" and not st.can_import
          and "stays readable" in st.message and f"${product.PRICE_USD}" in st.message)

    # -- activating ---------------------------------------------------------
    try:
        L.activate("not a key")
        check("a key with spaces in it is refused before anything is sent", False)
    except LicenceError:
        check("a key with spaces in it is refused before anything is sent", ls.calls == [])
    try:
        L.activate("MG-NO-SUCH-KEY")
        check("an unknown key is refused with Lemon Squeezy's own reason", False)
    except LicenceError as e:
        check("an unknown key is refused with Lemon Squeezy's own reason",
              "not found" in str(e) and not L.status().can_import, str(e))
    ls.offline = True
    try:
        L.activate("MG-GOOD-KEY-0001")
        check("offline, activating says so and changes nothing", False)
    except LicenceError as e:
        check("offline, activating says so and changes nothing",
              "Couldn't reach" in str(e) and L.status().state == "trial_over")
    ls.offline = False
    try:
        L.activate("OTHER-PRODUCT-KEY")
        check("a key for a different product is refused", False)
    except LicenceError as e:
        check("a key for a different product is refused", "different product" in str(e))
    check("and its activation is handed straight back, so it costs the buyer no seat",
          ls.calls[-1][1].endswith("/deactivate") and not ls.instances)

    ls.calls.clear()
    st = L.activate("  MG-GOOD-KEY-0001 \n")
    check("a good key activates, pasted with whitespace and all",
          st.state == "licensed" and st.can_import and st.key_hint == "MG-G…", st.message)
    method, url, sent = ls.calls[0]
    check("activation goes to the License API with only the key and a made-up name",
          method == "POST" and url == product.LICENCE_API + "activate"
          and set(sent) == {"license_key", "instance_name"})
    import getpass
    import socket
    check("the name it sends is random, not this Mac's name or yours",
          socket.gethostname().split(".")[0].lower() not in sent["instance_name"].lower()
          and getpass.getuser().lower() not in sent["instance_name"].lower(), sent["instance_name"])
    check("the call is in the same audit log as everything else, without the key in it",
          gate.audit and gate.audit[-1].purpose == "licence" and gate.audit[-1].allowed
          and "MG-GOOD-KEY-0001" not in gate.audit[-1].preview, gate.audit[-1].preview)
    check("the buyer's email is not kept", "buyer@example.com" not in data.path.read_text())
    check("a licensed copy imports again", L.can_import())

    # -- re-checking, and the offline grace -----------------------------------
    ls.calls.clear()
    clock[0] += timedelta(days=product.REVALIDATE_DAYS - 1)
    L.validate()
    check("the key isn't re-checked before it is due", ls.calls == [])
    clock[0] += timedelta(days=2)
    L.validate()
    check("once due, it is validated with the key and this Mac's activation id",
          len(ls.calls) == 1 and ls.calls[0][1].endswith("/validate")
          and set(ls.calls[0][2]) == {"license_key", "instance_id"})
    last_good = clock[0]
    ls.offline = True
    clock[0] += timedelta(days=product.REVALIDATE_DAYS + 1)
    st = L.validate()
    check("offline, a due check fails quietly and the licence carries on",
          st.state == "licensed" and st.can_import)
    n = len(ls.calls)
    clock[0] += timedelta(hours=1)
    L.validate()
    check("and it doesn't hammer the server: a failed check waits hours to retry",
          len(ls.calls) == n)
    ls.server_error, ls.offline = True, False
    clock[0] += timedelta(hours=7)
    check("a Lemon Squeezy outage counts as no answer, not as a no",
          L.validate().state == "licensed")
    ls.server_error, ls.offline = False, True
    clock[0] = last_good + timedelta(days=product.GRACE_DAYS + 1)
    st = L.validate()
    check(f"after {product.GRACE_DAYS} days without a check getting through, imports wait",
          st.state == "check_needed" and not st.can_import and "readable" in st.message)
    ls.offline = False
    clock[0] += timedelta(hours=7)
    st = L.validate()
    check("and one check that gets through puts it straight back",
          st.state == "licensed" and st.can_import)
    check("a clock set backwards is not taken as a lapse",
          Licence(data, transport=ls, gate=gate,
                  now=lambda: last_good - timedelta(days=400)).status().state == "licensed")

    # -- a definite no --------------------------------------------------------
    ls.keys["MG-GOOD-KEY-0001"]["status"] = "disabled"          # refunded
    st = L.validate(force=True)
    check("a refunded or disabled key ends the licence at the next check",
          st.state == "trial_over" and not st.can_import and "no longer active" in st.message)
    check("and the key is no longer kept on this Mac", "MG-GOOD-KEY-0001" not in data.path.read_text())
    ls.keys["MG-GOOD-KEY-0001"]["status"] = "active"

    # -- moving to another Mac ------------------------------------------------
    L.activate("MG-GOOD-KEY-0001")
    seats = len(ls.instances)
    ls.offline = True
    try:
        L.deactivate()
        check("offline, moving the licence says so and keeps it here", False)
    except LicenceError:
        check("offline, moving the licence says so and keeps it here",
              L.status().state == "licensed")
    ls.offline = False
    st = L.deactivate()
    check("deactivating hands the seat back and forgets the key",
          len(ls.instances) == seats - 1 and st.state == "trial_over"
          and "key" not in data.load())
    check("the trial doesn't restart because a licence was removed", not st.can_import)

    # -- the gate, for these calls -------------------------------------------
    closed = NetworkGate(PrivacySettings())          # local_only on, no consent for anything
    check("message content is still refused while licence calls go through",
          closed.settings.local_only
          and closed.authorize_service("licence", product.LICENCE_API + "validate",
                                       {"license_key": "k", "instance_id": "i"}) is not None)
    refusals = {
        "another address": ("licence", "https://evil.example/v1/licenses/activate", {}),
        "an extra field": ("licence", product.LICENCE_API + "activate",
                           {"license_key": "k", "text": "hi"}),
        "something message-sized": ("licence", product.LICENCE_API + "activate",
                                    {"license_key": "x" * 400}),
        "a purpose it doesn't know": ("telemetry", product.LICENCE_API, {}),
        "fields on the update check": ("updates", product.UPDATE_URL, {"license_key": "k"}),
    }
    for what, args in refusals.items():
        try:
            closed.authorize_service(*args)
            check(f"the gate refuses {what}", False)
        except ConsentError:
            check(f"the gate refuses {what}", not closed.audit[-1].allowed)
    check("exactly two kinds of call can carry no message content out",
          set(SERVICES) == {"licence", "updates"})
    sources = {p: p.read_text() for p in Path("app").rglob("*.py")}
    openers = sorted(str(p) for p, t in sources.items() if "urlopen(" in t)
    check("only the licence client and the gated Claude seam ever open a connection",
          openers == ["app/analysis/llm.py", "app/licence.py"], ", ".join(openers))
    check("and the update check goes through the licence client's transport",
          "UrllibTransport" in sources[Path("app/updates.py")]
          and "urlopen" not in sources[Path("app/updates.py")])

    # -- the update check -----------------------------------------------------
    was = (product.UPDATE_URL, SERVICES["updates"], product.SITE_URL)
    product.UPDATE_URL, product.SITE_URL = "https://site.test/version.json", "https://site.test"
    SERVICES["updates"] = ServiceRule(product.UPDATE_URL, frozenset(), exact=True)
    try:
        ls.calls.clear()
        off = NetworkGate(PrivacySettings(update_checks=False))
        info = updates.check(data, transport=ls, gate=off, now=now, force=True)
        check("switched off, the update check makes no call", ls.calls == [] and not info.checked)
        info = updates.check(data, transport=ls, gate=gate, now=now, force=True)
        check("switched on, it is one plain GET with nothing in it",
              ls.calls == [("GET", product.UPDATE_URL, {})] and info.newer and info.latest == "1.2.0")
        updates.check(data, transport=ls, gate=gate, now=now)
        check("and it asks at most once a week", len(ls.calls) == 1)
        ls.site = {"version": "9.9.9", "url": "https://phish.example/get"}
        info = updates.check(data, transport=ls, gate=gate, now=now, force=True)
        check("a download link off the product's own site is never offered",
              info.url == product.SITE_URL)
        check("versions compare as numbers, not strings",
              updates.is_newer("1.10.0", "1.9.9") and not updates.is_newer("1.1.0", "1.1.0"))
    finally:
        product.UPDATE_URL, SERVICES["updates"], product.SITE_URL = was
    check("with the placeholder site, no update check is attempted at all",
          not updates.check(AppData(root / "fresh"), transport=ls, gate=gate, force=True).checked)

    # -- through the importer and the API -------------------------------------
    from app import importer
    home = AppData()                                   # MG_HOME, set by validate.py
    saved = home.load()
    try:
        home.save({"trial_started": (datetime.now() - timedelta(days=product.TRIAL_DAYS + 3))
                   .isoformat(timespec="seconds")})
        r = importer.run_import(path=str(root / "no-such-chat.db"))
        check("after the trial the importer stops before reading anything, and says why",
              not r.ok and r.licence and "trial has ended" in r.note.lower())
        check("the WhatsApp import waits too", importer.run_whatsapp_import().licence)
    finally:
        home.save(saved)

    from fastapi.testclient import TestClient
    from app import main as server
    from app.security import COOKIE
    c = TestClient(server.app, base_url="http://127.0.0.1:8077")
    c.cookies.set(COOKIE, server.guard.session)
    o = {"origin": "http://127.0.0.1:8077"}
    got = c.get("/api/licence").json()
    check("the Privacy screen is told about both calls, where they go and what they carry",
          [u["purpose"] for u in got["network"]] == ["licence", "updates"]
          and all(u["to"] and u["sends"] and u["when"] for u in got["network"]))
    check("the overview carries the licence, worked out without a network call",
          "licence" in c.get("/api/overview").json())
    was_t = server.licence.transport, server.licence.data
    server.licence.transport, server.licence.data = ls, AppData(root / "api")
    try:
        check("another website can't activate or remove a licence",
              c.post("/api/licence/activate", json={"key": "MG-GOOD-KEY-0001"},
                     headers={"origin": "http://attacker.example"}).status_code == 403
              and c.post("/api/licence/deactivate").status_code == 403)
        r = c.post("/api/licence/activate", json={"key": "MG-NO-SUCH-KEY"}, headers=o)
        check("a refused key comes back as a sentence", r.status_code == 400 and "not found" in r.text)
        r = c.post("/api/licence/activate", json={"key": "MG-GOOD-KEY-0001"}, headers=o)
        check("and a good one activates from the Privacy screen",
              r.status_code == 200 and r.json()["state"] == "licensed")
        check("which leaves its mark in the privacy audit log",
              any(a["purpose"] == "licence" for a in c.get("/api/privacy").json()["audit"]))
        c.post("/api/privacy", json={"update_checks": False}, headers=o)
        check("switching update checks off is kept for the next launch",
              server.appdata.load().get("update_checks") is False
              and c.get("/api/licence").json()["update_checks"] is False)
        c.post("/api/privacy", json={"update_checks": True}, headers=o)
    finally:
        server.licence.transport, server.licence.data = was_t
    js = Path("app/static/app.js").read_text()
    check("the app shows the licence, what goes online, and the update switch",
          "drawLicence" in js and "What goes online" in js and "upd-toggle" in js)
    check("and no longer claims nothing at all can ever go online",
          "no request can leave this machine for any reason" not in js)
