#!/usr/bin/env bash
# ~30 s two-person meeting played through the speakers (Daniel = project lead, Samantha = analyst).
# Start an OnTrack session first (⌥⇧S), then run: scripts/demo_meeting.sh
set -euo pipefail
A=Daniel; B=Samantha
cue() { printf '\n\033[1;33m>>> %s\033[0m\n\n' "$1"; }

cue "Meeting starts in 3 s — keep OnTrack recording (⌥⇧S)."
sleep 3
say -v $A "Hi Samantha, thanks for joining. Quick update on the Leuven sensor project."
say -v $B "Sure. The good news is the calibration is done. We measured an error of two point three percent."
say -v $A "Great. What's the main risk right now?"
say -v $B "The data migration. It's scheduled for Wednesday, and we still don't have a rollback plan."
cue "Now press ⌥Space and ask:  What is the main risk and when is it scheduled?"
say -v $A "Okay. Can you write the rollback steps and send them to me before Tuesday evening?"
say -v $B "Yes, I'll send them tomorrow. And let's review everything on Thursday at ten."
say -v $A "Perfect. Thursday at ten it is. Thanks!"
cue "Done. Try also asking:  Who has to send what, and by when?   Then ⌥⇧S → Enter to save."
