#!/usr/bin/env bash
# OnTrack helper. Usage: scripts/dev.sh [all|setup|models|doctor|cert|test|app|run|reset-permissions]
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

prereqs() {
  local bad=0
  [ "$(uname -m)" = "arm64" ] || { echo "✗ Apple Silicon (M1 or newer) is required — MLX Whisper does not run on Intel Macs"; bad=1; }
  local major; major="$(sw_vers -productVersion | cut -d. -f1)"
  [ "$major" -ge 14 ] || { echo "✗ macOS 14 (Sonoma) or newer is required (you have $(sw_vers -productVersion))"; bad=1; }
  command -v swift >/dev/null 2>&1 || { echo "✗ Missing Apple Command Line Tools — run: xcode-select --install"; bad=1; }
  command -v uv >/dev/null 2>&1 || { echo "✗ Missing 'uv' — run: curl -LsSf https://astral.sh/uv/install.sh | sh   (then open a new Terminal)"; bad=1; }
  if ! command -v claude >/dev/null 2>&1; then
    echo "! Claude Code CLI not found — transcription works, but questions and summaries need it:"
    echo "    curl -fsSL https://claude.ai/install.sh | bash   then run 'claude' once to log in"
  fi
  local free_gb; free_gb="$(df -g "$HOME" | awk 'NR==2 {print $4}')"
  [ "${free_gb:-0}" -ge 10 ] || echo "! Only ${free_gb} GB free — models and Python packages need about 8 GB"
  [ "$ROOT" = "$HOME/Desktop/Work/OnTrack" ] || \
    echo "! Repo is at $ROOT — the app looks for ~/Desktop/Work/OnTrack. Run once: defaults write com.romandzhus.ontrack engineDir \"$ROOT/engine\""
  return $bad
}

# Order matters: Hugging Face access is checked BEFORE ~5 GB of models are downloaded.
setup() {
  prereqs || exit 1
  echo "→ Installing the engine's Python packages (one time, ~2 GB)…"
  (cd "$ROOT/engine" && uv sync --extra models)
  if ! (cd "$ROOT/engine" && uv run python -c "import huggingface_hub,sys; sys.exit(0 if huggingface_hub.get_token() else 1)"); then
    echo "→ Log in to Hugging Face. Paste a *Read* token from https://huggingface.co/settings/tokens"
    echo "  (answer 'n' when asked about git credentials)."
    (cd "$ROOT/engine" && uv run hf auth login)
  fi
  models
  echo "✓ engine ready ($ROOT/engine/.venv/bin/ontrack-engine)"
}

# Check access → download everything → verify the offline cache. Safe to rerun (downloads resume).
models() {
  (cd "$ROOT/engine" && uv run ontrack models download) || {
    echo "✗ Model setup stopped — fix the items above and run: scripts/dev.sh setup"; exit 1; }
}

doctor() {
  echo "== prerequisites"; prereqs && echo "✓ prerequisites"
  echo "== code signing"; has_cert && echo "✓ '$SIGN_ID' exists" || echo "! no signing certificate — run: scripts/dev.sh cert"
  echo "== engine"
  if [ -x "$ROOT/engine/.venv/bin/ontrack-engine" ]; then
    echo "✓ installed"
    echo "== Hugging Face access"; (cd "$ROOT/engine" && uv run ontrack models check) || true
    echo "== offline model cache"; (cd "$ROOT/engine" && HF_HUB_OFFLINE=1 uv run ontrack models verify) || true
  else
    echo "✗ not installed — run: scripts/dev.sh setup"
  fi
  echo "== app"; [ -d ~/Applications/OnTrack.app ] && echo "✓ ~/Applications/OnTrack.app" || echo "✗ not built — run: scripts/dev.sh app"
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
    # Always SwiftPM (works with or without Xcode): our own bundle gets the app icon and the
    # "OnTrack Local Signing" identity, so macOS keeps permissions across rebuilds.
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
  echo "✓ installed ~/Applications/OnTrack.app"
}

case "${1:-all}" in
  setup) setup ;;
  models) models ;;
  doctor) doctor ;;
  cert) cert ;;
  reset-permissions)
    tccutil reset Microphone com.romandzhus.ontrack
    tccutil reset ScreenCapture com.romandzhus.ontrack ;;
  test) test_all ;;
  app) app ;;
  run) open ~/Applications/OnTrack.app ;;
  all) setup; cert; test_all; app; open ~/Applications/OnTrack.app ;;
  *) echo "usage: scripts/dev.sh [all|setup|models|doctor|cert|test|app|run|reset-permissions]"; exit 2 ;;
esac
