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


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


PAGE = """<!doctype html><meta charset="utf-8"><body style="margin:0;height:100vh;display:grid;place-items:center;
background:#101113;color:#91979e;font:15px -apple-system,system-ui,sans-serif"><div style="text-align:center;max-width:460px;
padding:0 24px"><div style="color:#eaecee;font-size:19px;font-weight:600;margin-bottom:8px">{title}</div>{body}</div></body>"""


class Controller(NSObject):

    @objc.python_method
    def setup(self, background: bool) -> None:
        self.server = None                   # in-process server module, when not using the project's Python
        self.child = None                    # `mg serve` process, when using it
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
        self.webview.setNavigationDelegate_(self)
        self.window.setContentView_(self.webview)
        self.webview.loadHTMLString_baseURL_(PAGE.format(title="Opening Messaging Graph…",
                                                         body="Decrypting your data on this Mac."), None)

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
        self._item(menu, "Open " + APP_NAME, "show:", target=self)
        self._item(menu, "Reload data", "reloadData:", target=self)
        menu.addItem_(NSMenuItem.separatorItem())
        self._item(menu, "Import messages in Terminal…", "importMessages:", target=self)
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
            self.server = server
            self.launch_url = server.guard.launch_url
            AppHelper.callAfter(self._loaded)
        except Exception as e:                          # show the reason in the window, not a silent crash
            message = html.escape(str(e) or type(e).__name__)
            AppHelper.callAfter(lambda: self.webview.loadHTMLString_baseURL_(
                PAGE.format(title="Messaging Graph couldn't open your data", body=message), None))

    @objc.python_method
    def _start_child(self, project: Path) -> None:
        from app.vault import Vault
        self._stop_child()
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
            if self.child is not None or project_dir() is not None:
                try:
                    self._start_child(project_dir())       # a fresh server reads the latest import
                    AppHelper.callAfter(self._loaded)
                except Exception as e:
                    message = html.escape(str(e))
                    AppHelper.callAfter(lambda: self.webview.loadHTMLString_baseURL_(
                        PAGE.format(title="Couldn't reload your data", body=message), None))
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
        run_in_terminal("import")

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
        return True

    def applicationShouldTerminateAfterLastWindowClosed_(self, app):
        return False

    def applicationWillTerminate_(self, notification):
        self._stop_child()

    # -- navigation: only this app's own pages load inside the window ---------
    def webView_decidePolicyForNavigationAction_decisionHandler_(self, webview, action, handler):
        url = action.request().URL()
        if url is None or url.scheme() in ("about", "data") or url.host() in ("127.0.0.1", "localhost"):
            handler(1)                                  # allow
        else:
            NSWorkspace.sharedWorkspace().openURL_(url)  # anything else opens in your browser
            handler(0)


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
