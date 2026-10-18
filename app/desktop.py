"""Messaging Graph as a Mac app: its own window, a menu bar item, and start at login.

The app never reads the Messages database, so it never needs Full Disk Access. It
serves the encrypted import (or the demo, when there isn't one yet) on a private
port, shows it in a native window, and hands importing to Terminal -- the only thing
that should hold Full Disk Access, and only while an import runs.

Closing the window keeps the app in the menu bar; Quit (⌘Q or the menu) ends it.

Why the data server usually runs as a separate process: the key that decrypts your
data sits in the login Keychain, and macOS lets the program that created it -- the
project's own Python, from `./mg import` -- read it without asking. The packaged app
contains a different Python, which macOS would treat as a stranger and prompt for your
password. So when the project folder is where it was at build time, the app starts
`mg serve` with that trusted Python and simply shows it. Otherwise it serves in-process.
"""
from __future__ import annotations

import html
import os
import plistlib
import shlex
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import objc
from AppKit import (
    NSApp, NSApplication, NSApplicationActivationPolicyAccessory, NSApplicationActivationPolicyRegular,
    NSBackingStoreBuffered, NSImage, NSMakeRect, NSMenu, NSMenuItem, NSOffState, NSOnState, NSStatusBar,
    NSVariableStatusItemLength, NSWindow, NSWindowStyleMaskClosable, NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskResizable, NSWindowStyleMaskTitled, NSWorkspace,
)
from Foundation import NSURL, NSObject, NSURLRequest
from PyObjCTools import AppHelper

from app import importer
from app import refresh as refreshing
from WebKit import WKWebView, WKWebViewConfiguration

APP_NAME = "Messaging Graph"
LABEL = "com.messaginggraph.app"
LOGIN_AGENT = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
PROJECT = Path(__file__).resolve().parents[1]


def app_bundle() -> Path | None:
    """The installed .app this process runs from, if it's the packaged app."""
    if not getattr(sys, "frozen", False):
        return None
    return next((p for p in Path(sys.executable).resolve().parents if p.suffix == ".app"), None)


def login_item_enabled() -> bool:
    return LOGIN_AGENT.exists()


def set_login_item(on: bool, bundle: Path | None = None) -> None:
    """A per-user launch agent that opens the app quietly in the background at login."""
    bundle = bundle or app_bundle()
    if on and bundle:
        LOGIN_AGENT.parent.mkdir(parents=True, exist_ok=True)
        LOGIN_AGENT.write_bytes(plistlib.dumps({
            "Label": LABEL,
            "ProgramArguments": ["/usr/bin/open", "-g", "-a", str(bundle), "--args", "--background"],
            "RunAtLoad": True,
        }))
    elif not on and LOGIN_AGENT.exists():
        LOGIN_AGENT.unlink()


def run_in_terminal(command: str) -> None:
    """Run one of the app's own commands in Terminal, so Terminal's Full Disk Access applies
    rather than the app holding it permanently."""
    if app_bundle():
        line = f"{shlex.quote(sys.executable)} --cli {command}"
    else:
        line = f"cd {shlex.quote(str(PROJECT))} && ./mg {command}"
    line = line.replace("\\", "\\\\").replace('"', '\\"')
    subprocess.run(["osascript", "-e", 'tell application "Terminal"', "-e", f'do script "{line}"',
                    "-e", "activate", "-e", "end tell"], check=False)


def project_dir() -> Path | None:
    """The source project, whose Python created the Keychain item, if it's still in place."""
    if app_bundle():
        marker = Path(getattr(sys, "_MEIPASS", "")) / "macapp" / "project_path.txt"
        path = Path(marker.read_text().strip()) if marker.exists() else None
    else:
        path = PROJECT
    return path if path and (path / ".venv" / "bin" / "python").exists() and (path / "mg").exists() else None


# One port, kept, so the app is the same origin every time it opens. A browser
# files everything it remembers -- the theme, which drafts you put away -- under
# the origin, and a fresh random port each launch handed it an empty drawer. The
# choices live on the server now, but there is still no reason to move.
PREFERRED_PORT = 49201


def _free_port() -> int:
    for port in (PREFERRED_PORT, 0):
        try:
            with socket.socket() as s:
                # SO_REUSEADDR, because uvicorn sets it on the socket it will
                # actually listen on. Without it here the probe refuses a port
                # uvicorn would take: quit the app and reopen it, and the one
                # you just closed is still in TIME_WAIT, so every quick restart
                # fell back to a random port and moved the origin after all.
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
        except OSError:
            continue        # genuinely taken by something else; any port will do
    raise RuntimeError("No port to serve on.")


# Shown before the real UI loads, so it follows the system the way the app does.
# It cannot use styles.css -- the server it would come from is what we're waiting
# for -- so the two neutrals are repeated here and nowhere else.
PAGE = """<!doctype html><meta charset="utf-8"><style>
:root{color-scheme:light dark;--page:#f6f5f2;--text:#4a4f55;--head:#1c1e21}
@media (prefers-color-scheme:dark){:root{--page:#191b1f;--text:#b3bac2;--head:#edeff2}}
body{margin:0;height:100vh;display:grid;place-items:center;background:var(--page);
color:var(--text);font:15px -apple-system,system-ui,sans-serif}
.t{color:var(--head);font-size:19px;font-weight:600;margin-bottom:8px}
</style><body><div style="text-align:center;max-width:460px;padding:0 24px">
<div class="t">{title}</div>{body}</div></body>"""


def _page(title: str, body: str) -> str:
    """Fill PAGE in by name.

    Not str.format: PAGE carries a stylesheet, and every CSS block in it reads as a
    replacement field, so formatting it raises KeyError on the first selector. The
    app died on launch that way, and only in the built bundle -- nothing else loads
    this page.
    """
    return PAGE.replace("{title}", title).replace("{body}", body)


# The one screen that used to be two Terminal commands and a trip through
# System Settings. It can't flip the switch for you -- macOS reserves that --
# but it can open the right pane and wait, rather than describing where to click.
PERMISSION = """<!doctype html><meta charset="utf-8"><style>
:root{color-scheme:light dark;--page:#f6f5f2;--text:#4a4f55;--head:#1c1e21;
--line:#dcd9d2;--card:#fffefb;--accent:#3c6ea5}
@media (prefers-color-scheme:dark){:root{--page:#191b1f;--text:#b3bac2;--head:#edeff2;
--line:#2f3339;--card:#1f2228;--accent:#7aa9d8}}
body{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--page);
color:var(--text);font:15px/1.6 -apple-system,system-ui,sans-serif}
.w{max-width:470px;padding:32px 26px}
h1{color:var(--head);font-size:21px;margin:0 0 10px}
ol{padding-left:20px;margin:18px 0}li{margin:7px 0}
b{color:var(--head)}
.row{display:flex;gap:10px;margin-top:20px;flex-wrap:wrap}
a.btn{display:inline-block;padding:9px 15px;border-radius:9px;text-decoration:none;
border:1px solid var(--line);color:var(--head);background:var(--card)}
a.primary{background:var(--accent);border-color:var(--accent);color:#fff}
.note{margin-top:20px;padding-top:14px;border-top:1px solid var(--line);font-size:13px}
</style><body><div class="w">
<h1>Messaging Graph needs permission to read your messages</h1>
<p>macOS keeps <b>~/Library/Messages</b> behind Full Disk Access. Nothing is read
until you grant it, and nothing leaves this Mac afterwards — the import writes
an encrypted copy here and the key goes in your Keychain.</p>
<ol>
<li>Open the settings pane below.</li>
<li>Switch on <b>Messaging Graph</b>.</li>
<li>Come back and press <b>Reopen</b> — a permission granted now only reaches
this app once it has started again.</li>
</ol>
<div class="row">
<a class="btn primary" href="mg://settings">Open Full Disk Access</a>
<a class="btn" href="mg://relaunch">Reopen Messaging Graph</a>
</div>
<div class="note">You can switch it back off whenever you like. The app only
reads chat.db during an import; serving your data never touches it.</div>
</div></body>"""


def _permission_page() -> str:
    return PERMISSION


class Controller(NSObject):

    @objc.python_method
    def setup(self, background: bool) -> None:
        self.server = None                   # in-process server module, when not using the project's Python
        self.child = None                    # `mg serve` process, when using it
        self.importing = False               # one import at a time
        self.hourly = None                   # the thing that keeps the data current
        self.refresh_item = None
        self.pending_refresh = False         # landed while you were reading; applied later
        self.launch_url = None
        self.background = background
        self._build_main_menu()
        self._build_window()
        self._build_status_item()
        if background:
            NSApp.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
        else:
            self.show_(None)

    # -- construction --------------------------------------------------------
    @objc.python_method
    def _item(self, menu, title, action, key="", target=None):
        item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, key)
        if target is not None:
            item.setTarget_(target)
        menu.addItem_(item)
        return item

    @objc.python_method
    def _build_main_menu(self) -> None:
        bar = NSMenu.alloc().init()
        for title, entries in [
            (APP_NAME, [("About " + APP_NAME, "orderFrontStandardAboutPanel:", ""), None,
                        ("Hide " + APP_NAME, "hide:", "h"), ("Quit " + APP_NAME, "terminate:", "q")]),
            # Without an Edit menu, ⌘C / ⌘V don't reach the web view -- and drafts are for copying.
            ("Edit", [("Undo", "undo:", "z"), ("Redo", "redo:", "Z"), None, ("Cut", "cut:", "x"),
                      ("Copy", "copy:", "c"), ("Paste", "paste:", "v"), ("Select All", "selectAll:", "a")]),
            ("View", [("Reload", "reloadPage:", "r")]),
            ("Window", [("Close", "performClose:", "w"), ("Minimize", "performMiniaturize:", "m")]),
        ]:
            holder = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, None, "")
            sub = NSMenu.alloc().initWithTitle_(title)
            for entry in entries:
                if entry is None:
                    sub.addItem_(NSMenuItem.separatorItem())
                else:
                    item = self._item(sub, *entry)
                    if entry[1] == "reloadPage:":
                        item.setTarget_(self)
            holder.setSubmenu_(sub)
            bar.addItem_(holder)
        NSApp.setMainMenu_(bar)

    @objc.python_method
    def _build_window(self) -> None:
        style = (NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
                 | NSWindowStyleMaskMiniaturizable | NSWindowStyleMaskResizable)
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, 1280, 860), style, NSBackingStoreBuffered, False)
        self.window.setTitle_(APP_NAME)
        self.window.setMinSize_((760, 560))
        self.window.setReleasedWhenClosed_(False)
        self.window.setDelegate_(self)
        self.window.setFrameAutosaveName_("MessagingGraphMain")
        if not self.window.setFrameUsingName_("MessagingGraphMain"):
            self.window.center()
        self.webview = WKWebView.alloc().initWithFrame_configuration_(
            self.window.contentView().bounds(), WKWebViewConfiguration.alloc().init())
        self.webview.setAutoresizingMask_(18)          # width + height
        # So the page can tell it is inside this app and offer the import as a
        # button. The same server answers a browser and a phone on the Wi-Fi,
        # where mg:// means nothing, so this has to be per-client.
        self.webview.setCustomUserAgent_(
            "Mozilla/5.0 (Macintosh) MessagingGraph/desktop")
        self.webview.setNavigationDelegate_(self)
        self.window.setContentView_(self.webview)
        self.webview.loadHTMLString_baseURL_(
            _page("Opening Messaging Graph…", "Decrypting your data on this Mac."), None)

    @objc.python_method
    def _build_status_item(self) -> None:
        self.status = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
        image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(
            "bubble.left.and.bubble.right", APP_NAME)
        if image is not None:
            image.setTemplate_(True)
            self.status.button().setImage_(image)
        else:
            self.status.button().setTitle_("MG")
        menu = NSMenu.alloc().init()
        menu.setAutoenablesItems_(False)               # otherwise AppKit overrides setEnabled_ below
        self._item(menu, "Open " + APP_NAME, "show:", target=self)
        self._item(menu, "Reload data", "reloadData:", target=self)
        menu.addItem_(NSMenuItem.separatorItem())
        self._item(menu, "Import my messages…", "importMessages:", target=self)
        self._item(menu, "Refresh now", "refreshNow:", target=self)
        self.refresh_item = self._item(menu, "Refresh every hour", "toggleHourly:", target=self)
        self._item(menu, "Check privacy in Terminal…", "checkPrivacy:", target=self)
        menu.addItem_(NSMenuItem.separatorItem())
        self.login_item = self._item(menu, "Start at login", "toggleLogin:", target=self)
        self.login_item.setState_(NSOnState if login_item_enabled() else NSOffState)
        if app_bundle() is None:
            self.login_item.setEnabled_(False)         # only the installed app can start at login
        menu.addItem_(NSMenuItem.separatorItem())
        self._item(menu, "Quit " + APP_NAME, "terminate:", "q")
        self.status.setMenu_(menu)

    # -- the server ----------------------------------------------------------
    @objc.python_method
    def boot(self) -> None:
        try:
            project = project_dir()
            if project is not None:
                self._start_child(project)
                AppHelper.callAfter(self._loaded)
                AppHelper.callAfter(self.start_hourly)
                return
            from app.vault import Vault
            port = _free_port()
            os.environ["MG_PORT"] = str(port)
            os.environ["MG_SOURCE"] = "vault" if Vault().exists() else "synthetic"
            import uvicorn
            from app import main as server
            config = uvicorn.Config(server.app, host="127.0.0.1", port=port, access_log=False,
                                    server_header=False, log_level="warning")
            threading.Thread(target=uvicorn.Server(config).run, daemon=True).start()
            for _ in range(400):
                try:
                    socket.create_connection(("127.0.0.1", port), 0.1).close()
                    break
                except OSError:
                    time.sleep(0.05)
            else:
                raise RuntimeError("The data server never answered.")
            self.server = server
            self.launch_url = server.guard.launch_url
            AppHelper.callAfter(self._loaded)
            AppHelper.callAfter(self.start_hourly)
        except Exception as e:                          # show the reason in the window, not a silent crash
            self._show_error("Messaging Graph couldn't open your data", str(e) or type(e).__name__)

    @objc.python_method
    def _start_child(self, project: Path) -> None:
        from app.vault import Vault
        self._stop_child()
        # macOS keeps Desktop, Documents and Downloads behind a permission an app
        # has to be granted, and a double-clicked app is not a terminal: it gets
        # refused where your shell is not. The data server is the project's own
        # Python, run from the project, so if that folder can't be read here it
        # cannot be read there either -- and the failure arrives as a server that
        # starts and never answers. Ask the cheap question first.
        try:
            os.listdir(project)
        except PermissionError:
            # One paragraph: _show_error escapes the message, so a line break here
            # would arrive as a space.
            raise RuntimeError(
                f"macOS won't let Messaging Graph read the project at {project} — "
                "that folder is one it protects, and an app has to be allowed in even "
                "where your terminal already is. Allow it under System Settings › "
                "Privacy & Security › Files and Folders, then open the app again. "
                "Keeping the project outside Desktop, Documents and Downloads avoids "
                "the question entirely."
            ) from None
        port = _free_port()
        cmd = [str(project / ".venv" / "bin" / "python"), "-m", "app.cli", "serve", "--port", str(port)]
        if not Vault().exists():
            cmd.append("--synthetic")
        self.child = subprocess.Popen(cmd, cwd=project, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                      text=True, bufsize=1)
        seen, deadline = [], time.time() + 180
        while time.time() < deadline:
            line = self.child.stdout.readline()
            if not line:
                if self.child.poll() is not None:
                    raise RuntimeError("".join(seen[-6:]).strip() or "The data server stopped while starting.")
                continue
            seen.append(line)
            if "?token=" in line:
                self.launch_url = line.strip()
                break
        else:
            raise RuntimeError("The data server took too long to start.")
        threading.Thread(target=lambda: [None for _ in self.child.stdout], daemon=True).start()  # keep the pipe drained
        for _ in range(400):
            try:
                socket.create_connection(("127.0.0.1", port), 0.1).close()
                return
            except OSError:
                time.sleep(0.05)
        raise RuntimeError("The data server printed its link but never answered.")

    @objc.python_method
    def _stop_child(self) -> None:
        if self.child is not None and self.child.poll() is None:
            self.child.terminate()
            try:
                self.child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.child.kill()
        self.child = None

    @objc.python_method
    def _show_error(self, title: str, message: str) -> None:
        body = html.escape(message)
        AppHelper.callAfter(lambda: self.webview.loadHTMLString_baseURL_(
            _page(title, body), None))

    @objc.python_method
    def _loaded(self) -> None:
        self._update_subtitle()
        url = NSURL.URLWithString_(self.launch_url)
        self.webview.loadRequest_(NSURLRequest.requestWithURL_(url))

    @objc.python_method
    def _update_subtitle(self) -> None:
        from app.vault import Vault
        if not Vault().exists():
            self.window.setSubtitle_("Demo data · import your messages from the menu bar")
        else:
            when = (Vault().manifest() or {}).get("imported_at", "")[:10]
            self.window.setSubtitle_(f"Your messages · imported {when}" if when else "Your messages")

    # -- actions -------------------------------------------------------------
    def show_(self, sender):
        NSApp.setActivationPolicy_(NSApplicationActivationPolicyRegular)
        self.window.makeKeyAndOrderFront_(None)
        NSApp.activateIgnoringOtherApps_(True)

    def reloadPage_(self, sender):
        self.webview.reload_(None)

    def reloadData_(self, sender):
        def work():
            project = project_dir()                        # once: it can move while the app is open
            if project is not None:
                try:
                    self._start_child(project)             # a fresh server reads the latest import
                    AppHelper.callAfter(self._loaded)
                except Exception as e:
                    self._show_error("Couldn't reload your data", str(e) or type(e).__name__)
                return
            if self.child is not None:
                self._show_error("Couldn't reload your data",
                                 "The project folder has moved, so the data server can't be restarted. "
                                 "Quit Messaging Graph and open it again.")
                return
            if self.server is None:
                return
            from app.vault import Vault
            state = self.server.state
            if state.source_name == "synthetic" and Vault().exists():
                state.source_name = "vault"            # first import since the app started
            state.reload()
            AppHelper.callAfter(self._update_subtitle)
            AppHelper.callAfter(lambda: self.webview.reload_(None))
        threading.Thread(target=work, daemon=True).start()

    def importMessages_(self, sender):
        self.beginImport()

    # -- keeping it current ---------------------------------------------------
    @objc.python_method
    def start_hourly(self) -> None:
        self.hourly = refreshing.Hourly(on_done=self._refreshed,
                                        should_defer=self._window_in_use)
        self.hourly.start()
        if self.refresh_item is not None:
            self.refresh_item.setState_(
                NSOnState if self.hourly.status.enabled else NSOffState)

    @objc.python_method
    def _window_in_use(self) -> bool:
        """Reading something is a good enough reason to wait an hour."""
        try:
            return bool(self.window.isVisible())
        except Exception:
            return False

    @objc.python_method
    def _refreshed(self, results) -> None:
        """New data landed. Show it now if nobody is looking, else when they next are."""
        if self._window_in_use():
            self.pending_refresh = True
            return
        AppHelper.callAfter(self.reloadData_, None)

    def toggleHourly_(self, sender):
        if self.hourly is None:
            return
        on = not self.hourly.status.enabled
        self.hourly.set_enabled(on)
        sender.setState_(NSOnState if on else NSOffState)

    def refreshNow_(self, sender):
        if self.hourly is None or self.importing:
            return
        threading.Thread(target=self.hourly.run_now, daemon=True).start()

    def checkPrivacy_(self, sender):
        run_in_terminal("doctor")

    def toggleLogin_(self, sender):
        set_login_item(not login_item_enabled())
        self.login_item.setState_(NSOnState if login_item_enabled() else NSOffState)

    # -- window and app delegate ---------------------------------------------
    def windowShouldClose_(self, sender):
        # Closing hides the window and the Dock icon; the app stays in the menu bar.
        self.window.orderOut_(None)
        NSApp.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
        return False

    def applicationShouldHandleReopen_hasVisibleWindows_(self, app, visible):
        self.show_(None)
        # Data arrived while this was open and was held back rather than
        # reloading the page under your hands. Now is the moment.
        if self.pending_refresh:
            self.pending_refresh = False
            AppHelper.callAfter(self.reloadData_, None)
        return True

    def applicationShouldTerminateAfterLastWindowClosed_(self, app):
        return False

    def applicationWillTerminate_(self, notification):
        self._stop_child()

    # -- navigation: only this app's own pages load inside the window ---------
    def webView_decidePolicyForNavigationAction_decisionHandler_(self, webview, action, handler):
        url = action.request().URL()
        if url is not None and url.scheme() == "mg":
            # The page's way of asking the app for something only the app can do.
            # Reading chat.db is granted to Messaging Graph, not to the Python
            # running the data server, so the import has to happen in here.
            handler(0)
            what = (url.host() or "").lower()
            if what == "import":
                AppHelper.callAfter(self.beginImport)
            elif what == "settings":
                NSWorkspace.sharedWorkspace().openURL_(
                    NSURL.URLWithString_(importer.SETTINGS_URL))
            elif what == "relaunch":
                AppHelper.callAfter(self.relaunch)
            return
        if url is None or url.scheme() in ("about", "data") or url.host() in ("127.0.0.1", "localhost"):
            handler(1)                                  # allow
        else:
            NSWorkspace.sharedWorkspace().openURL_(url)  # anything else opens in your browser
            handler(0)

    # -- importing, without a terminal ---------------------------------------
    @objc.python_method
    def beginImport(self) -> None:
        """Ask once whether we can read, then do the slow part off the main thread."""
        if self.importing:
            return
        got = importer.access()
        if not got.ok:
            self._show_permission(got)
            return
        self.importing = True
        self._show_busy("Importing your messages", "Starting…")
        threading.Thread(target=self._import_work, daemon=True).start()

    @objc.python_method
    def _import_work(self) -> None:
        try:
            result = importer.run_import(
                on_step=lambda line: AppHelper.callAfter(
                    self._show_busy, "Importing your messages", line))
        except Exception as e:                           # a broken read, not a crash
            self.importing = False
            self._show_error("The import stopped", str(e) or type(e).__name__)
            return
        self.importing = False
        if not result.ok:
            self._show_error("Nothing was imported", result.note)
            return
        AppHelper.callAfter(self._import_done, result)

    @objc.python_method
    def _import_done(self, result) -> None:
        # The server was started against the demo corpus, or an older vault, so
        # it has to come back up before the window shows what just landed.
        self._show_busy("Imported", f"{result.messages:,} messages from "
                                    f"{result.people:,} people. Reopening…")
        self.reloadData_(None)

    @objc.python_method
    def _show_permission(self, got) -> None:
        if got.missing:
            self._show_error("No messages to read", got.reason)
            return
        self.webview.loadHTMLString_baseURL_(_permission_page(), None)

    @objc.python_method
    def _show_busy(self, title: str, line: str) -> None:
        self.webview.loadHTMLString_baseURL_(_page(title, html.escape(line)), None)

    @objc.python_method
    def relaunch(self) -> None:
        """Quit and come back, because a permission granted now reaches this
        process only once it has started again."""
        path = str(app_bundle() or "")
        if path:
            subprocess.Popen(["/usr/bin/open", "-n", "-a", path])
        NSApp.terminate_(None)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["--cli"]:                            # e.g. "--cli import", run inside Terminal
        from app.cli import main as cli_main
        return cli_main(argv[1:])
    app = NSApplication.sharedApplication()
    controller = Controller.alloc().init()
    app.setDelegate_(controller)
    controller.setup("--background" in argv)
    threading.Thread(target=controller.boot, daemon=True).start()
    AppHelper.runEventLoop()
    return 0
