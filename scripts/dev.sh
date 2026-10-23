#!/usr/bin/env bash
# OnTrack dev helper. Usage: scripts/dev.sh [setup|test|app|run|all]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
need() { command -v "$1" >/dev/null 2>&1 || { echo "✗ Missing '$1' — $2"; exit 1; }; }

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
  (cd "$ROOT/app/OnTrackCore" && swift test)
}

app() {
  need xcodegen "brew install xcodegen"
  need xcodebuild "install Xcode"
  cd "$ROOT/app"
  xcodegen generate --quiet
  xcodebuild -project OnTrack.xcodeproj -scheme OnTrack -configuration Release \
             -derivedDataPath build -quiet build
  mkdir -p ~/Applications
  rm -rf ~/Applications/OnTrack.app
  cp -R build/Build/Products/Release/OnTrack.app ~/Applications/
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
