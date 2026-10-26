# OnTrack — Manual Acceptance Checklist (spec §10)

Run after `scripts/dev.sh all` (and `scripts/dev.sh doctor` shows no ✗). Tick each box; note failures with the time and what you saw.

## Setup
- [ ] Engine log says "models verified — offline mode" (no Hugging Face requests after setup).
- [ ] App launches as a menu-bar icon (no Dock icon); engine log `~/Library/Application Support/OnTrack/logs/engine.log` shows "engine listening".
- [ ] Menu → Engine shows `idle` (model loads within ~30 s on first launch).
- [ ] Enroll My Voice: talk 30 s → toast "Voiceprint saved (match ≥ 0.80)".

## In person (2 people, MacBook mic)
- [ ] ⌥⇧S starts; overlay appears top-center with a red dot and timer.
- [ ] Your lines show a **blue** dot (Me), the other person's **amber** (Other).
- [ ] New lines appear ≤ 3 s after a sentence ends; only the last 3 lines show.
- [ ] Ukrainian ↔ English ↔ Dutch switching is transcribed in the right language (never translated, never Russian).
- [ ] ⌥Space → ask "what did they just say about X?" → streamed answer in the overlay; Esc returns focus to your previous app.
- [ ] ⌥⇧S stops → end card → Enter → progress (Separating speakers → Re-transcribing → Summarizing → Saving) → "Saved to notes" → Open shows `notes/<session>/summary.md` with Speaker A/B labels in transcript.md.

## Online call
- [ ] Zoom/Meet with **headphones**: remote voices show **green** (Them), yours blue.
- [ ] Same call on **laptop speakers**: remote voices are not duplicated as Other/Me lines (≤ 1 stray line per minute).
- [ ] Zoom/Meet volume is not ducked while OnTrack listens.

## Robustness
- [ ] `pkill -9 -f ontrack-engine` mid-session → overlay shows "reconnecting…", then continues within 5 s; transcript.md has no duplicated or lost lines (≤ 10 s gap noted as `[gap …]`).
- [ ] Unplug/plug AirPods mid-session → capture continues.
- [ ] Rename `claude` temporarily (or log out) → asking shows an error; ending the session still saves the transcript and `summary_pending`; menu → Retry Summary works after fixing.
- [ ] Discard needs a second click and removes the session folder.
- [ ] 3-hour session (lecture or a long YouTube video): engine memory stays < 4 GB (Activity Monitor), no crash.

## Notes sync
- [ ] After saving, `notes/<session>/.pending_sync` exists; after asking Claude in Cowork to "sync OnTrack notes", the notes appear in the OnTrack Claude Project and the marker is gone.
