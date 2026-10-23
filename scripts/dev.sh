#!/usr/bin/env bash
# OnTrack dev helper. Usage: scripts/dev.sh [setup|test|app|run|all]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
need() { command -v "$1" >/dev/null 2>&1 || { echo "✗ Missing '$1' — $2"; exit 1; }; }
has_xcode() { ! xcode-select -p 2>/dev/null | grep -q CommandLineTools && command -v xcodebuild >/dev/null 2>&1; }

setup() {
  need uv "brew install uv"
  need claude "npm i -g @anthropic-ai/claude-code, then run 'claude' once to log in"
  (cd "$ROOT/engine" && uv sync --extra models)
  echo "✓ engine ready ($ROOT/engine/.venv/bin/ontrack-engine)"
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
    cat > "$app/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleIdentifier</key><string>com.romandzhus.ontrack</string>
  <key>CFBundleName</key><string>OnTrack</string>
  <key>CFBundleDisplayName</key><string>OnTrack</string>
  <key>CFBundleExecutable</key><string>OnTrack</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>0.1.0</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>LSUIElement</key><true/>
  <key>NSMicrophoneUsageDescription</key><string>OnTrack transcribes lectures, meetings and calls locally on this Mac.</string>
</dict>
</plist>
PLIST
    codesign --force --sign - "$app"
  fi
  echo "✓ installed ~/Applications/OnTrack.app"
}

case "${1:-all}" in
  setup) setup ;;
  test) test_all ;;
  app) app ;;
  run) open ~/Applications/OnTrack.app ;;
  all) setup; test_all; app; open ~/Applications/OnTrack.app ;;
  *) echo "usage: scripts/dev.sh [setup|test|app|run|all]"; exit 2 ;;
esac
