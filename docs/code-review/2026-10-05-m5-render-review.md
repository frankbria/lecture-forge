# Code Review — M5 `render` (2026-10-05)

**Scope:** `src/lecture_forge/render.py`, the `render` command in `cli.py`, `retryable_status` (renamed from `providers._retryable` so render shares one retry rule), `tests/test_render.py`.
**Risk:** high for **cost** (every call spends ElevenLabs credits), medium for data integrity (files land in your Dropbox).
**Plan:** cost control on every path to an ElevenLabs call, data integrity (cache, atomic publish, what ends up in Dropbox), external-call reliability, path safety for LLM-written titles, secret handling. Skipped web and ML.

| # | Priority | Finding | Resolution | Status |
|---|---|---|---|---|
| 1 | **Critical** | Credits must never be spent unasked. | Per-episode and total characters plus the plan balance are printed first; over the balance is refused; `Render? [y/N]` otherwise; `--yes` skips; with no terminal and no `--yes`, it refuses. Tests: "n" and no terminal → zero TTS calls. | Fixed |
| 2 | High | A failure at piece 5 of 6 must not cost pieces 1–4 again. | Per-piece cache keyed on (model, voice, seed, format, text), written via `.part` + rename. Resume and edit-only-the-changed-piece are tested. Identical pieces are deduped, which a test caught. | Fixed |
| 3 | High | Dropbox must never sync a half-written MP3. | ffmpeg writes a hidden `.part` file, then `os.replace`. No MP3 is published when a piece fails (tested). | Fixed |
| 4 | High | If `D:` isn't mounted, `/mnt/d/Dropbox/Lectures` would silently become a Linux-only folder, so the MP3s never reach Dropbox. | The output folder is created only when its parent exists; otherwise a clear error (tested; nothing is created). | Fixed |
| 5 | Medium | LLM-written titles become Windows filenames. | `safe_filename` strips `<>:"/\|?*` and control characters, trims trailing dots and spaces, prefixes reserved names, caps at 120 characters (8 cases tested). Tags keep the original title. | Fixed |
| 6 | Medium | **Found in review:** renaming an episode in `plan.yaml` after rendering left `02 - Old.mp3` *and* `02 - New.mp3`, so the car playlist would have a duplicate. | The previous output (from `render.json`) is removed after the new file is published, but only an `.mp3` for that episode number inside the output folder (a guard test checks a file outside it survives). | Fixed |
| 7 | Medium | `eleven_v3` rejects request stitching; other models benefit from it. | `previous_request_ids` only for stitching models, at most the 3 most recent (tested); v3 gets none (tested on the adapter). | Fixed |
| 8 | Medium | Transient API failures. | 408/409/429/5xx and network errors retried 10/20/40 s with the shared rule; 400/401 fail at once (tested). SDK timeout 300 s per request. | Fixed |
| 9 | Low | Error messages include ElevenLabs' response body. | It never contains the API key (the key is only sent as a header); kept for diagnosis. | Accepted |
| 10 | Low | The balance lookup could fail (network, API change). | Returns `None` with a warning; render still asks for confirmation. | Accepted |
| 11 | Note | Old cached pieces accumulate in `episodes/NN/audio/` after edits (~1 MB per minute of audio). | — | Accepted: local disk only; delete the folder to reclaim space |
| 12 | Note | Two renders of the same episode at once aren't coordinated. | — | Accepted: single-user CLI |

**Verification:** 188 unit tests, with joins and tags checked by real ffmpeg/ffprobe on real MP3 bytes. One paid integration test (opt-in). A real tiny render: two real `eleven_v3` pieces in your voice (392 characters), joined into a 29.1 s MP3 with correct tags; a second run was skipped at 0 characters.
