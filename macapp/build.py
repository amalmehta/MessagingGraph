"""Build Messaging Graph.app, install it, and start it at login.

    uv sync --group app          # once: the packaging tools
    ./mg build-app               # build, install, start at login
    ./mg build-app --no-login    # same, without the login item

The app bundles its own Python and this project's code as it is when you build, so
rebuild after pulling changes. Your data isn't in the app: it stays encrypted in
~/Library/Application Support/messaging-graph.
"""
from __future__ import annotations

import math
import plistlib
import shutil
import struct
import subprocess
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
NAME = "Messaging Graph"
BUNDLE_ID = "com.messaginggraph.app"


def _png(path: Path, size: int = 1024, pad: int = 100, radius: int = 185) -> None:
    """Draw the icon: people as nodes, connected, on a warm charcoal squircle.

    The artwork is laid out on a 1024 grid, so draw it at 1024 and scale down --
    at a small `size` the nodes fall off the canvas. `pad=0, radius=0` gives the
    same picture full-bleed and fully opaque, which is what a home-screen icon
    wants: iOS rounds and masks it itself, and a transparent corner there comes
    out as a black one.
    """
    def clamp(x):
        return 0.0 if x < 0 else 1.0 if x > 1 else x

    nodes = [((330, 600), 118, (88, 166, 255)), ((655, 410), 138, (227, 135, 60)), ((690, 700), 92, (63, 185, 80))]
    edges = [(nodes[0][0], nodes[1][0]), (nodes[1][0], nodes[2][0]), (nodes[0][0], nodes[2][0])]
    rows = bytearray()
    for y in range(size):
        rows.append(0)
        t = y / size
        top, bottom = (38, 40, 45), (22, 23, 26)
        bg = [top[i] + (bottom[i] - top[i]) * t for i in range(3)]
        for x in range(size):
            # rounded square signed distance
            qx = abs(x + 0.5 - size / 2) - (size / 2 - pad - radius)
            qy = abs(y + 0.5 - size / 2) - (size / 2 - pad - radius)
            d = math.hypot(max(qx, 0), max(qy, 0)) + min(max(qx, qy), 0) - radius
            a = clamp(0.5 - d)
            if a == 0:
                rows += b"\x00\x00\x00\x00"
                continue
            r, g, b = bg
            for (x1, y1), (x2, y2) in edges:
                dx, dy = x2 - x1, y2 - y1
                u = clamp(((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy))
                dist = math.hypot(x - (x1 + u * dx), y - (y1 + u * dy))
                k = clamp(15 - dist) * 0.45
                r, g, b = r + (232 - r) * k, g + (228 - g) * k, b + (222 - b) * k
            for (cx, cy), rad, col in nodes:
                k = clamp(rad + 0.5 - math.hypot(x - cx, y - cy))
                ring = clamp(rad + 14.5 - math.hypot(x - cx, y - cy)) - k
                r, g, b = r + (22 - r) * ring, g + (23 - g) * ring, b + (26 - b) * ring
                r, g, b = r + (col[0] - r) * k, g + (col[1] - g) * k, b + (col[2] - b) * k
            rows += bytes((int(r), int(g), int(b), int(a * 255)))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(bytes(rows), 9)) + chunk(b"IEND", b""))


def make_icon() -> Path:
    iconset = BUILD / "icon.iconset"
    shutil.rmtree(iconset, ignore_errors=True)
    iconset.mkdir(parents=True)
    master = BUILD / "icon_1024.png"
    _png(master)
    for base in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            px = base * scale
            name = f"icon_{base}x{base}{'@2x' if scale == 2 else ''}.png"
            subprocess.run(["sips", "-z", str(px), str(px), str(master), "--out", str(iconset / name)],
                           check=True, capture_output=True)
    icns = BUILD / "icon.icns"
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(icns)], check=True)
    return icns


def build_bundle(icns: Path) -> Path:
    import PyInstaller.__main__
    # Where the project lives, so the app can run your data server with the project's own
    # Python -- the one your Keychain already trusts with the encryption key.
    marker = BUILD / "project_path.txt"
    marker.write_text(str(ROOT))
    PyInstaller.__main__.run([
        str(ROOT / "macapp" / "entry.py"), "--name", NAME, "--windowed", "--noconfirm", "--clean",
        "--icon", str(icns), "--osx-bundle-identifier", BUNDLE_ID,
        "--distpath", str(BUILD / "dist"), "--workpath", str(BUILD / "work"), "--specpath", str(BUILD),
        "--paths", str(ROOT),
        "--add-data", f"{ROOT / 'app' / 'static'}:app/static",
        "--add-data", f"{marker}:macapp",
        "--collect-submodules", "app", "--collect-submodules", "uvicorn",
        "--collect-submodules", "keyring", "--copy-metadata", "keyring",
        "--collect-data", "vaderSentiment", "--hidden-import", "PyObjCTools.AppHelper",
        "--log-level", "WARN",
    ])
    bundle = BUILD / "dist" / f"{NAME}.app"
    info_path = bundle / "Contents" / "Info.plist"
    info = plistlib.loads(info_path.read_bytes())
    info.update({
        "CFBundleDisplayName": NAME,
        "CFBundleShortVersionString": "1.1.0",
        "LSApplicationCategoryType": "public.app-category.social-networking",
        "NSHighResolutionCapable": True,
        # Shown when macOS asks whether the app may open Terminal for an import.
        "NSAppleEventsUsageDescription": "Messaging Graph opens Terminal to import your messages, so only "
                                         "Terminal needs Full Disk Access, and only while importing.",
        # Without these, macOS has no words to put in the dialog -- so it never
        # asks, and quietly refuses instead. The app runs the project's own Python
        # from wherever the project lives, which is often one of these folders.
        **{f"NS{folder}FolderUsageDescription":
           f"Messaging Graph runs its data server from your project folder, so it needs to read it "
           f"if you keep the project in {folder}."
           for folder in ("Desktop", "Documents", "Downloads")},
    })
    info_path.write_bytes(plistlib.dumps(info))
    # Editing Info.plist invalidates the signature; sign again.
    sign(bundle)
    return bundle


IDENTITY = "Messaging Graph Dev"


def signing_identity() -> str | None:
    """The app's own signing identity, if this Mac has one."""
    out = subprocess.run(["security", "find-identity", "-v", "-p", "codesigning"],
                         capture_output=True, text=True).stdout
    return IDENTITY if f'"{IDENTITY}"' in out else None


def make_identity() -> str | None:
    """Create one, once, so the signature stops moving.

    An ad-hoc signature has no identity behind it, so macOS pins a permission
    to the binary's hash -- and every rebuild changes the hash. Full Disk
    Access, granted properly, stopped working the next time the app was built,
    with the switch still showing as on. A self-signed certificate gives the
    app one identity to be, and the grant survives.

    macOS asks you to approve the new certificate; nothing here can do that
    for you, so a refusal just falls back to ad hoc.
    """
    import tempfile
    home = Path.home() / "Library" / "Keychains" / "login.keychain-db"
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        cert, key, p12 = d / "cert.pem", d / "key.pem", d / "id.p12"
        made = subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
             "-keyout", str(key), "-out", str(cert), "-days", "3650",
             "-subj", f"/CN={IDENTITY}",
             # Exactly one basicConstraints: -x509 writes its own, and adding a
             # second made every critical extension unreadable to macOS.
             "-addext", "keyUsage=critical,digitalSignature,keyCertSign",
             "-addext", "extendedKeyUsage=codeSigning"],
            capture_output=True)
        if made.returncode:
            return None
        # SHA-1 MAC and 3DES: macOS cannot read the PKCS#12 OpenSSL 3 writes by default.
        if subprocess.run(["openssl", "pkcs12", "-export", "-out", str(p12),
                           "-inkey", str(key), "-in", str(cert), "-passout", "pass:mg",
                           "-name", IDENTITY, "-macalg", "sha1",
                           "-certpbe", "PBE-SHA1-3DES", "-keypbe", "PBE-SHA1-3DES"],
                          capture_output=True).returncode:
            return None
        if subprocess.run(["security", "import", str(p12), "-k", str(home),
                           "-P", "mg", "-T", "/usr/bin/codesign", "-A"],
                          capture_output=True).returncode:
            return None
        # Needs your approval, and without it the identity is not usable.
        subprocess.run(["security", "add-trusted-cert", "-r", "trustRoot",
                        "-p", "codeSign", "-k", str(home), str(cert)],
                       capture_output=True)
    return signing_identity()


def sign(bundle: Path) -> str:
    """Sign with the app's own identity where there is one, ad hoc otherwise."""
    who = signing_identity() or make_identity()
    args = ["codesign", "--force", "--deep", "--sign", who or "-", str(bundle)]
    done = subprocess.run(args, capture_output=True, text=True)
    if done.returncode and who:                   # a broken identity should not stop the build
        subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(bundle)],
                       check=True, capture_output=True)
        return "ad hoc"
    if done.returncode:
        raise RuntimeError(done.stderr.strip() or "codesign failed")
    return who or "ad hoc"


def install(bundle: Path) -> Path:
    system_apps = Path("/Applications")
    target_dir = system_apps if system_apps.is_dir() and _writable(system_apps) else Path.home() / "Applications"
    target_dir.mkdir(exist_ok=True)
    target = target_dir / bundle.name
    # Quit a running copy; if it's stuck (say, behind a system dialog), stop it.
    try:
        subprocess.run(["osascript", "-e", f'tell application "{NAME}" to quit'], capture_output=True, timeout=10)
    except subprocess.TimeoutExpired:
        pass
    subprocess.run(["pkill", "-f", f"{NAME}.app/Contents/MacOS/"], capture_output=True)
    subprocess.run(["pkill", "-f", "app.cli serve --port"], capture_output=True)   # the app's own data server
    if target.exists():
        shutil.rmtree(target)
    subprocess.run(["ditto", str(bundle), str(target)], check=True)
    return target


def _writable(path: Path) -> bool:
    probe = path / ".mg-write-test"
    try:
        probe.touch()
        probe.unlink()
        return True
    except OSError:
        return False


def main(login: bool = True) -> int:
    try:
        import AppKit  # noqa: F401
        import PyInstaller  # noqa: F401
        import WebKit  # noqa: F401
    except ImportError:
        print("  xx  The packaging tools aren't installed. Run:  uv sync --group app")
        return 1
    BUILD.mkdir(exist_ok=True)
    print("Drawing the icon…", flush=True)
    icns = make_icon()
    print("Packaging the app (a minute or two)…", flush=True)
    bundle = build_bundle(icns)
    target = install(bundle)
    print(f"  ok  Installed {target}")
    # Worth saying out loud: it decides whether the permissions you granted
    # are still yours after this build.
    who = signing_identity()
    if who:
        print(f"  ok  Signed as {who} — permissions you grant survive a rebuild")
    else:
        print("  !!  Signed ad hoc. macOS ties Full Disk Access to the exact binary, so "
              "this build has lost any you granted: remove Messaging Graph from that list "
              "and add it again.")
    from app.desktop import set_login_item
    set_login_item(login, target)
    print("  ok  Starts at login" if login else "  ok  Won't start at login")
    subprocess.run(["open", str(target)])
    print(f"\nOpened {NAME}. Close its window and it stays in the menu bar; quit from there.")
    return 0


if __name__ == "__main__":
    sys.exit(main(login="--no-login" not in sys.argv))
