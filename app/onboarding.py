"""The first thing a new customer sees: what the app needs, and why.

One screen, shown in the Mac app's window the first time it opens on a Mac with
nothing imported. It asks macOS -- cheaply, by trying to read sixteen bytes --
whether Full Disk Access has been granted, says so, and offers the three things
that can be done about it from here: open the right System Settings pane, check
again, and reopen the app. The window re-checks on its own every couple of
seconds while this is up, so switching the setting on is usually enough.

Kept apart from desktop.py, which needs AppKit, so the decisions and the page can
be tested anywhere.
"""
from __future__ import annotations

import html

from . import product
from .appdata import AppData

GRANTED, NOT_YET, NO_MESSAGES = "granted", "not_yet", "no_messages"


def needed(data: AppData, have_data: bool) -> bool:
    """First run: never finished onboarding, and nothing imported yet. Someone
    upgrading with a vault already in place has done all of this before."""
    return not have_data and not data.load().get("onboarded")


def mark_done(data: AppData) -> None:
    data.update(onboarded=True)


def access_state(got) -> str:
    """`importer.Access` as one of three words."""
    if got.ok:
        return GRANTED
    return NO_MESSAGES if got.missing else NOT_YET


PAGE = """<!doctype html><meta charset="utf-8"><style>
:root{color-scheme:light dark;--page:#f6f5f2;--text:#4a4f55;--head:#1c1e21;
--line:#dcd9d2;--card:#fffefb;--accent:#3c6ea5;--ok:#2f7a45;--wait:#9a5b13}
@media (prefers-color-scheme:dark){:root{--page:#191b1f;--text:#b3bac2;--head:#edeff2;
--line:#2f3339;--card:#1f2228;--accent:#7aa9d8;--ok:#6cc58a;--wait:#e0a45c}}
body{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--page);
color:var(--text);font:15px/1.6 -apple-system,system-ui,sans-serif}
.w{max-width:540px;padding:36px 26px}
h1{color:var(--head);font-size:23px;margin:0 0 6px}
h2{color:var(--head);font-size:16px;margin:0 0 4px;display:flex;gap:10px;align-items:center}
.lede{margin:0 0 22px}
.step{border:1px solid var(--line);background:var(--card);border-radius:12px;
padding:16px 18px;margin:14px 0}
.pill{font-size:12px;font-weight:600;padding:2px 9px;border-radius:20px;border:1px solid}
.pill.granted{color:var(--ok)}.pill.not_yet,.pill.no_messages{color:var(--wait)}
.row{display:flex;gap:10px;margin-top:12px;flex-wrap:wrap}
a.btn,span.btn{display:inline-block;padding:8px 14px;border-radius:9px;text-decoration:none;
border:1px solid var(--line);color:var(--head);background:var(--page);font-size:14px}
a.primary{background:var(--accent);border-color:var(--accent);color:#fff}
span.btn{opacity:.45;cursor:default}
b{color:var(--head)}
a{color:var(--accent)}
.small{font-size:13px;margin-top:10px}
.note{margin-top:22px;padding-top:14px;border-top:1px solid var(--line);font-size:13px}
</style><body><div class="w">
<h1>Welcome to {name}</h1>
<p class="lede">It reads your Messages history and shows who you've drifted from, plans
that never got a date, and messages worth sending. All of it happens on this Mac:
your messages are never uploaded, to us or anyone.</p>
<div class="step">
<h2>1. Let it read your messages <span class="pill {state}">{state_label}</span></h2>
{access_body}
</div>
<div class="step">
<h2>2. Import</h2>
<p style="margin:0">Reads a snapshot once and keeps an encrypted copy here, with the key in
your Keychain. Takes a minute or two.</p>
<div class="row">{import_button}
<a class="btn" href="mg://demo">Look around with made-up people first</a></div>
</div>
<div class="note">{trial}<br>Only two things ever go online, and neither carries anything
from your messages: the licence check (once you buy) and an update check you can switch
off under Privacy.</div>
</div></body>"""

ACCESS = {
    GRANTED: """<p style="margin:0">Done — macOS lets {name} read Messages. It only reads during
an import; you can switch it back off whenever you like and what you imported stays.</p>""",
    NOT_YET: """<p style="margin:0">macOS keeps <b>~/Library/Messages</b> behind
<b>Full Disk Access</b>, so nothing can read your messages until you say so. {name} only
reads them during an import, and only on this Mac.</p>
<div class="row"><a class="btn primary" href="mg://settings">Open Full Disk Access settings</a>
<a class="btn" href="mg://recheck">Check again</a></div>
<p class="small">Switch on <b>{name}</b> in that list. If it isn't there, press <b>+</b> and
choose it from Applications. This page notices by itself; if it still says
“Not yet” after you've switched it on, <a href="mg://relaunch">reopen {name}</a> —
macOS sometimes only tells an app once it has started again.</p>""",
    NO_MESSAGES: """<p style="margin:0">There's no Messages history on this Mac to read. Sign in to
Messages with your Apple Account and let it sync, then
<a href="mg://recheck">check again</a>.</p>""",
}
LABEL = {GRANTED: "Allowed", NOT_YET: "Not yet", NO_MESSAGES: "No Messages yet"}


def page(state: str, licence_status=None) -> str:
    """The onboarding screen for one Full Disk Access state."""
    name = html.escape(product.NAME)
    if licence_status is not None:
        trial = html.escape(licence_status.message)
    else:
        trial = (f"Free for {product.TRIAL_DAYS} days with everything working, then "
                 f"${product.PRICE_USD} once.")
    button = ('<a class="btn primary" href="mg://import">Import my messages</a>'
              if state == GRANTED else '<span class="btn">Import my messages</span>')
    return (PAGE.replace("{access_body}", ACCESS[state])
            .replace("{import_button}", button)
            .replace("{state_label}", LABEL[state])
            .replace("{state}", state)
            .replace("{trial}", trial)
            .replace("{name}", name))


def licence_page(message: str) -> str:
    """Shown instead of an import once the trial is over."""
    name = html.escape(product.NAME)
    return (PAGE.split("<body>")[0] + f"""<body><div class="w">
<h1>Importing needs a licence now</h1>
<p class="lede">{html.escape(message)}</p>
<div class="row"><a class="btn primary" href="mg://buy">Buy {name} — ${product.PRICE_USD}</a>
<a class="btn" href="mg://licence">I have a key</a>
<a class="btn" href="mg://home">Back to my data</a></div>
<div class="note">Buying opens the checkout in your browser. The key arrives by email;
paste it under Privacy → Licence.</div>
</div></body>""")
