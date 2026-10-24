#!/usr/bin/env bash
# Collects OnTrack diagnostics into diag/report.txt (inside the repo) so they can be inspected.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SUP="$HOME/Library/Application Support/OnTrack"
OUT="$ROOT/diag"; mkdir -p "$OUT"
{
  echo "== date: $(date)"
  echo "== processes"; pgrep -lf "OnTrack|ontrack-engine"
  echo "== permissions (TCC) — needs Full Disk Access to read, may be empty"
  echo "== codesign"; codesign -dv ~/Applications/OnTrack.app 2>&1 | grep -E "Identifier|Authority|Signature"
  echo "== app support"; ls -la "$SUP" "$SUP/audio" 2>&1
  echo "== engine.log (last 120)"; tail -n 120 "$SUP/logs/engine.log" 2>&1
  echo "== engine-stdout.log errors (last 60)"; grep -nE "Error|Traceback|Exception|WARNING" "$SUP/logs/engine-stdout.log" 2>/dev/null | tail -n 60
  echo "== crash reports"; ls -t ~/Library/Logs/DiagnosticReports/OnTrack* 2>/dev/null | head -3
  latest=$(ls -t ~/Library/Logs/DiagnosticReports/OnTrack* 2>/dev/null | head -1)
  [ -n "$latest" ] && head -c 6000 "$latest"
  echo; echo "== audio levels"
  cd "$ROOT/engine" && uv run python - "$SUP/audio" <<'PY'
import sys, pathlib, numpy as np, soundfile as sf
root = pathlib.Path(sys.argv[1])
sessions = sorted([p for p in root.iterdir() if p.is_dir()], key=lambda p: p.stat().st_mtime)[-3:] if root.exists() else []
if not sessions:
    print("no audio folders (deleted after finalize, or nothing recorded)")
for s in sessions:
    print("session", s.name)
    for ch in ("mic", "system"):
        files = sorted(s.glob(f"{ch}-*.flac"))
        if not files:
            print(f"  {ch}: no files"); continue
        x = np.concatenate([sf.read(str(f), dtype="float32")[0] for f in files])
        if x.ndim > 1: x = x.mean(axis=1)
        print(f"  {ch}: {len(x)/16000:.1f} s, rms={np.sqrt(np.mean(x**2)) if len(x) else 0:.5f}, peak={np.abs(x).max() if len(x) else 0:.4f}")
PY
} > "$OUT/report.txt" 2>&1
echo "✓ wrote $OUT/report.txt"
