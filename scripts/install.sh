#!/usr/bin/env bash
# One-command OnTrack install for a fresh Mac. Safe to rerun: it skips whatever is already done.
#   git clone https://github.com/dzhusjr/OnTrack.git ~/Desktop/Work/OnTrack && ~/Desktop/Work/OnTrack/scripts/install.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
ask() { local a=""; read -r -p "$1 [y/N] " a || true; [[ "$a" =~ ^[Yy] ]]; }

say "1/5  Checking your Mac"
[ "$(uname -m)" = "arm64" ] || { echo "✗ OnTrack needs an Apple Silicon Mac (M1 or newer)."; exit 1; }
[ "$(sw_vers -productVersion | cut -d. -f1)" -ge 14 ] || { echo "✗ macOS 14 (Sonoma) or newer is required."; exit 1; }
echo "✓ Apple Silicon, macOS $(sw_vers -productVersion)"

say "2/5  Tools (Apple command-line tools, uv, Claude Code)"
if ! command -v swift >/dev/null 2>&1; then
  xcode-select --install 2>/dev/null || true
  echo "A window opened to install Apple's command-line tools. When it finishes, run this script again."
  exit 0
fi
command -v uv >/dev/null 2>&1 || curl -LsSf https://astral.sh/uv/install.sh | sh
command -v uv >/dev/null 2>&1 || { echo "✗ uv did not install. Open a new Terminal and run this script again."; exit 1; }
echo "✓ swift, uv"
if ! command -v claude >/dev/null 2>&1; then
  echo "Claude Code writes the answers and summaries (it needs a Claude subscription). Transcription works without it."
  if ask "Install Claude Code now?"; then
    curl -fsSL https://claude.ai/install.sh | bash
    echo "→ When this script is done, run 'claude' once to log in."
  fi
fi
WANT_LOCAL=0
echo "Optional: a small AI model that runs on this Mac (works offline, uses about 2.5 GB of memory only while loaded)."
if ask "Set it up too?"; then WANT_LOCAL=1; fi

say "3/5  Hugging Face account (free, one time)"
if [ -f "$HOME/.cache/huggingface/token" ] || [ -n "${HF_TOKEN:-}" ]; then
  echo "✓ already logged in"
else
  echo "The speaker-separation models are free, but Hugging Face asks you to accept their terms first."
  echo "I will open the pages. In your browser: 1) log in (confirm your email), 2) on each of the 4 model pages click"
  echo "'Agree and access repository', 3) on the tokens page create a token of type Read and copy it."
  read -r -p "Press Return to open the pages… " _ || true
  open "https://huggingface.co/join" 2>/dev/null || true
  for m in pyannote/segmentation-3.0 pyannote/speaker-diarization-3.1 pyannote/speaker-diarization-community-1 \
           pyannote/wespeaker-voxceleb-resnet34-LM; do
    open "https://huggingface.co/$m" 2>/dev/null || true
  done
  open "https://huggingface.co/settings/tokens" 2>/dev/null || true
  read -r -p "When you have the token copied, press Return. You will paste it in the next step… " _ || true
fi

say "4/5  Installing OnTrack (about 5 GB of downloads, 10 to 25 minutes)"
"$ROOT/scripts/dev.sh" setup
"$ROOT/scripts/dev.sh" cert
"$ROOT/scripts/dev.sh" app

if [ "$WANT_LOCAL" = 1 ]; then
  say "Local AI model"
  "$ROOT/scripts/dev.sh" local-ai || echo "! Local AI setup did not finish. Run scripts/dev.sh local-ai later."
fi

say "5/5  Done"
echo "OnTrack is in your menu bar. Its Permissions card opens on first launch: grant Microphone and System audio,"
echo "then Enroll My Voice (30 seconds of you talking)."
echo "Check anything later with: scripts/dev.sh doctor"
"$ROOT/scripts/dev.sh" run
