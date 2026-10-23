#!/usr/bin/env bash
# Package Messaging Graph for sale: the .app and a DMG, in dist/.
#
#   scripts/package.sh                Developer ID signature, notarized and stapled
#   scripts/package.sh --no-notarize  Developer ID signature, not sent to Apple
#   scripts/package.sh --adhoc        ad-hoc signature, for trying the build on this Mac
#
# A release needs two things in the environment, and refuses without them:
#   MG_SIGN_IDENTITY   the certificate's name: "Developer ID Application: Your Name (TEAMID)"
#   MG_NOTARY_PROFILE  a notarytool keychain profile (xcrun notarytool store-credentials)
# Neither is a secret: the certificate's private key and the app-specific password
# stay in your login Keychain. Setting them up is in docs/GO-LIVE.md.
#
# Nothing here installs the app, adds a login item or touches your data.
set -euo pipefail
cd "$(dirname "$0")/.."

MODE=release
for arg in "$@"; do
  case "$arg" in
    --adhoc) MODE=adhoc ;;
    --no-notarize) MODE=signed ;;
    -h|--help) sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "  xx  Unknown option: $arg (try --help)" >&2; exit 2 ;;
  esac
done

refuse() { echo "  xx  $*" >&2; exit 1; }
say() { echo "==> $*"; }

[[ "$(uname)" == Darwin ]] || refuse "This builds a Mac app, so it has to run on a Mac."

# Everything that can stop a release is checked before the minutes of building,
# not discovered after them. First, whether the two settings are there at all.
missing=()
[[ $MODE != adhoc && -z "${MG_SIGN_IDENTITY:-}" ]] && missing+=(MG_SIGN_IDENTITY)
[[ $MODE == release && -z "${MG_NOTARY_PROFILE:-}" ]] && missing+=(MG_NOTARY_PROFILE)
if (( ${#missing[@]} )); then
  refuse "Not set: ${missing[*]}. A build for sale needs both:
      export MG_SIGN_IDENTITY=\"Developer ID Application: Your Name (TEAMID)\"
      export MG_NOTARY_PROFILE=messaging-graph   # from: xcrun notarytool store-credentials
      Setting them up is docs/GO-LIVE.md, steps 2 and 3.
      To sign without notarizing: scripts/package.sh --no-notarize
      To try a build on this Mac only: scripts/package.sh --adhoc"
fi
if [[ $MODE != adhoc ]]; then
  [[ "$MG_SIGN_IDENTITY" == "Developer ID Application:"* ]] || refuse "MG_SIGN_IDENTITY must name a
      \"Developer ID Application: ...\" certificate. Apple Development and Mac App Store
      certificates can't be notarized for direct sale."
  security find-identity -v -p codesigning | grep -qF "\"$MG_SIGN_IDENTITY\"" || refuse "No signing
      identity called \"$MG_SIGN_IDENTITY\" is in your keychains, or its private key is
      missing. 'security find-identity -v -p codesigning' lists the ones you have."
fi
if [[ $MODE == release ]]; then
  xcrun --find notarytool >/dev/null 2>&1 || refuse "notarytool isn't installed. Install Xcode, or the
      Command Line Tools: xcode-select --install"
fi

if command -v uv >/dev/null 2>&1; then
  say "Installing the packaging tools (uv sync --group app)"
  uv sync --group app --frozen --quiet
fi
PY=.venv/bin/python
[[ -x $PY ]] || refuse "No .venv here. Install uv, then: uv sync --group app"

if [[ $MODE == release ]]; then
  $PY -m app.product --check-release || refuse "app/product.py isn't ready to sell yet (above)."
fi

VERSION=$($PY -m app.product)
DIST=dist
APP="$DIST/Messaging Graph.app"
DMG="$DIST/MessagingGraph-$VERSION.dmg"
ENT=macapp/entitlements.plist
mkdir -p "$DIST"
rm -rf "$APP" "$DMG"

say "Building Messaging Graph $VERSION ($(uname -m))"
$PY -m macapp.build --release --dist "$DIST" | tail -1

if [[ $MODE == adhoc ]]; then
  SIGN=(codesign --force --options runtime --sign -)
else
  SIGN=(codesign --force --options runtime --timestamp --sign "$MG_SIGN_IDENTITY")
fi

say "Signing inside out, with the hardened runtime"
# Every library and extension module first, then the bundle, which signs the main
# executable with the entitlements. --deep would sign everything with the app's
# entitlements, which Apple advises against.
signed=0
while IFS= read -r -d '' f; do
  if file -b "$f" | grep -q "Mach-O"; then
    "${SIGN[@]}" "$f" 2>/dev/null
    signed=$((signed + 1))
  fi
done < <(find "$APP/Contents" -type f ! -path "*/Contents/MacOS/*" -print0)
if [[ $MODE == adhoc ]]; then
  # Library validation only lets a hardened app load code signed by its own Team
  # ID, and an ad-hoc signature has none, so the bundled Python would be refused.
  # A Developer ID build signs everything with one team and needs no exception;
  # only this local test build gets it.
  ENT_USED=$(mktemp -d)/entitlements.plist
  cp "$ENT" "$ENT_USED"
  /usr/libexec/PlistBuddy -c "Add :com.apple.security.cs.disable-library-validation bool true" "$ENT_USED"
else
  ENT_USED=$ENT
fi
"${SIGN[@]}" --entitlements "$ENT_USED" "$APP"
codesign --verify --deep --strict "$APP" || refuse "The signature doesn't verify."
echo "    $signed libraries and the app signed ($([[ $MODE == adhoc ]] && echo "ad hoc" || echo "$MG_SIGN_IDENTITY"))"

notarize() {   # $1: a .zip or .dmg to send to Apple
  local out id status
  out=$(xcrun notarytool submit "$1" --keychain-profile "$MG_NOTARY_PROFILE" --wait \
        --output-format json) || refuse "notarytool couldn't submit $1. Is the profile right?
      xcrun notarytool history --keychain-profile \"$MG_NOTARY_PROFILE\""
  id=$(printf '%s' "$out" | $PY -c 'import json,sys; print(json.load(sys.stdin).get("id",""))')
  status=$(printf '%s' "$out" | $PY -c 'import json,sys; print(json.load(sys.stdin).get("status",""))')
  [[ $status == Accepted ]] || refuse "Apple didn't accept $1 ($status). Why:
      xcrun notarytool log $id --keychain-profile \"$MG_NOTARY_PROFILE\""
  echo "    accepted ($id)"
}

if [[ $MODE == release ]]; then
  say "Notarizing the app (Apple usually takes a few minutes)"
  ZIPDIR=$(mktemp -d)
  ditto -c -k --keepParent "$APP" "$ZIPDIR/app.zip"
  notarize "$ZIPDIR/app.zip"
  rm -rf "$ZIPDIR"
  xcrun stapler staple -q "$APP"
fi

say "Making the disk image"
STAGE=$(mktemp -d)
ditto "$APP" "$STAGE/Messaging Graph.app"
ln -s /Applications "$STAGE/Applications"
hdiutil create -quiet -volname "Messaging Graph" -srcfolder "$STAGE" -fs HFS+ \
  -format UDZO -ov "$DMG"
rm -rf "$STAGE"

if [[ $MODE != adhoc ]]; then
  codesign --force --timestamp --sign "$MG_SIGN_IDENTITY" "$DMG"
fi
if [[ $MODE == release ]]; then
  say "Notarizing the disk image"
  notarize "$DMG"
  xcrun stapler staple -q "$DMG"
  say "Asking Gatekeeper, as a customer's Mac would"
  spctl -a -t exec -vv "$APP"
  spctl -a -t open --context context:primary-signature -vv "$DMG"
fi

echo
echo "  ok  $APP"
echo "  ok  $DMG ($(du -h "$DMG" | cut -f1), sha256 $(shasum -a 256 "$DMG" | cut -c1-16)…)"
case $MODE in
  adhoc)  echo "  !!  Ad-hoc signed: for this Mac only. Gatekeeper refuses it anywhere else,"
          echo "      and Full Disk Access granted to it won't carry over to a signed build." ;;
  signed) echo "  !!  Signed but not notarized: Gatekeeper still blocks it on other Macs." ;;
  release) echo "  ok  Signed, notarized and stapled. Ready to upload (docs/GO-LIVE.md, step 6)." ;;
esac
