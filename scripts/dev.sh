#!/usr/bin/env bash
# OnTrack dev helper. Usage: scripts/dev.sh [setup|test|app|run|all]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
need() { command -v "$1" >/dev/null 2>&1 || { echo "✗ Missing '$1' — $2"; exit 1; }; }
SIGN_ID="OnTrack Local Signing"
has_cert() { security find-identity -v -p codesigning 2>/dev/null | grep -q "$SIGN_ID"; }

# One-time: a self-signed code-signing identity, so macOS keeps Microphone / Screen Recording
# permissions across rebuilds (ad-hoc signatures change on every build).
cert() {
  if has_cert; then echo "✓ '$SIGN_ID' already exists"; return; fi
  local tmp; tmp="$(mktemp -d)"
  cat > "$tmp/cfg" <<'CFG'
[req]
distinguished_name=dn
x509_extensions=ext
prompt=no
[dn]
CN=OnTrack Local Signing
[ext]
basicConstraints=critical,CA:false
keyUsage=critical,digitalSignature
extendedKeyUsage=critical,codeSigning
CFG
  /usr/bin/openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -config "$tmp/cfg" \
    -keyout "$tmp/key.pem" -out "$tmp/cert.pem" 2>/dev/null
  /usr/bin/openssl pkcs12 -export -inkey "$tmp/key.pem" -in "$tmp/cert.pem" -name "$SIGN_ID" \
    -out "$tmp/id.p12" -passout pass:ontrack
  security import "$tmp/id.p12" -k ~/Library/Keychains/login.keychain-db -P ontrack -T /usr/bin/codesign
  echo "→ macOS will ask for your login password to trust the certificate for code signing:"
  security add-trusted-cert -r trustRoot -p codeSign -k ~/Library/Keychains/login.keychain-db "$tmp/cert.pem"
  rm -rf "$tmp"
  has_cert && echo "✓ created '$SIGN_ID'" || { echo "✗ certificate not trusted for code signing"; exit 1; }
}

has_xcode() { ! xcode-select -p 2>/dev/null | grep -q CommandLineTools && command -v xcodebuild >/dev/null 2>&1; }

setup() {
  need uv "brew install uv"
  need claude "npm i -g @anthropic-ai/claude-code, then run 'claude' once to log in"
  (cd "$ROOT/engine" && uv sync --extra models)
  echo "✓ engine ready ($ROOT/engine/.venv/bin/ontrack-engine)"
  echo "→ downloading Whisper models (live: large-v3-turbo, saved transcripts: large-v3) — one time, ~4.5 GB"
  (cd "$ROOT/engine" && uv run hf download mlx-community/whisper-large-v3-turbo >/dev/null && \
                        uv run hf download mlx-community/whisper-large-v3-mlx >/dev/null)
  if ! ls ~/.cache/huggingface/token >/dev/null 2>&1; then
    echo "→ Log in to Hugging Face once so the app-launched engine can load pyannote:"
    (cd "$ROOT/engine" && uv run hf auth login)
  fi
}

test_all() {
  (cd "$ROOT/engine" && uv run pytest -q)
  if has_xcode; then
    (cd "$ROOT/app/OnTrackCore" && swift test)
  else
    echo "• Skipping Swift core tests (XCTest needs full Xcode; they run in CI). Building still works."
    (cd "$ROOT/app/OnTrackCore" && swift build -q)
  fi
}

app() {
  cd "$ROOT/app"
  mkdir -p ~/Applications
  if has_xcode && command -v xcodegen >/dev/null 2>&1; then
    xcodegen generate --quiet
    xcodebuild -project OnTrack.xcodeproj -scheme OnTrack -configuration Release \
               -derivedDataPath build -quiet build
    rm -rf ~/Applications/OnTrack.app
    cp -R build/Build/Products/Release/OnTrack.app ~/Applications/
  else
    # Command Line Tools only: build with SwiftPM and assemble the .app bundle ourselves.
    swift build -c release --product OnTrack
    local bin app
    bin="$(swift build -c release --show-bin-path)/OnTrack"
    app=~/Applications/OnTrack.app
    rm -rf "$app"
    mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources"
    cp "$bin" "$app/Contents/MacOS/OnTrack"
    cp "$ROOT/app/Resources/AppIcon.icns" "$app/Contents/Resources/AppIcon.icns"
    cat > "$app/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleIdentifier</key><string>com.romandzhus.ontrack</string>
  <key>CFBundleName</key><string>OnTrack</string>
  <key>CFBundleDisplayName</key><string>OnTrack</string>
  <key>CFBundleExecutable</key><string>OnTrack</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>0.1.0</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>LSUIElement</key><true/>
  <key>NSMicrophoneUsageDescription</key><string>OnTrack transcribes lectures, meetings and calls locally on this Mac.</string>
</dict>
</plist>
PLIST
    if has_cert; then
      codesign --force --sign "$SIGN_ID" "$app"
    else
      echo "• Signing ad-hoc — run 'scripts/dev.sh cert' once so permissions survive rebuilds."
      codesign --force --sign - "$app"
    fi
  fi
  echo "✓ installed ~/Applications/OnTrack.app"
}

case "${1:-all}" in
  setup) setup ;;
  cert) cert ;;
  reset-permissions)
    tccutil reset Microphone com.romandzhus.ontrack
    tccutil reset ScreenCapture com.romandzhus.ontrack ;;
  test) test_all ;;
  app) app ;;
  run) open ~/Applications/OnTrack.app ;;
  all) setup; cert; test_all; app; open ~/Applications/OnTrack.app ;;
  *) echo "usage: scripts/dev.sh [setup|cert|test|app|run|reset-permissions|all]"; exit 2 ;;
esac
