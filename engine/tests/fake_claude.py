#!/usr/bin/env python3
"""Stand-in for the `claude` CLI. Behaviour via FAKE_CLAUDE_MODE; argv/stdin dumped to FAKE_CLAUDE_LOG."""
import json
import os
import sys
import time

mode = os.environ.get("FAKE_CLAUDE_MODE", "deltas")
prompt = sys.stdin.read()
log = os.environ.get("FAKE_CLAUDE_LOG")
if log:
    with open(log, "w") as f:
        json.dump({"argv": sys.argv[1:], "stdin": prompt, "cwd": os.getcwd()}, f)


def emit(o):
    print(json.dumps(o), flush=True)


emit({"type": "system", "subtype": "init"})
if mode == "deltas":
    for t in ["Hel", "lo ", "Roman"]:
        emit({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": t}}})
    emit({"type": "assistant", "message": {"content": [{"type": "text", "text": "Hello Roman"}]}})
    emit({"type": "result", "subtype": "success", "is_error": False, "result": "Hello Roman"})
elif mode == "assistant":
    emit({"type": "assistant", "message": {"content": [{"type": "text", "text": "Whole answer"}]}})
    emit({"type": "result", "subtype": "success", "is_error": False, "result": "Whole answer"})
elif mode == "sleep":
    time.sleep(30)
elif mode == "ratelimit":
    emit({"type": "result", "subtype": "error", "is_error": True, "result": "Claude AI usage limit reached (429)"})
elif mode == "auth":
    print("Invalid API key · Please run /login", file=sys.stderr)
    sys.exit(1)
