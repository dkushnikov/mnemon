# Media-chain fixtures (GH #26, part 1)

Recorded tool output from the 2026-10-28 field test, used by `tests/test_media.sh` (the stub `yt-dlp` and the
stub `youtube_transcript_api` replay them) and read by `media-extract.py`'s wall classification tests:

| File | What it is |
|---|---|
| `ytdlp-bot-wall.stderr.txt` | `yt-dlp --dump-single-json` stderr behind YouTube's bot wall (the version warning, then the `Sign in to confirm you’re not a bot` line — note the typographic apostrophe) |
| `transcript-api-ip-block.txt` | the message of `youtube_transcript_api`'s `IpBlocked` / `RequestBlocked` exception from a blocked host |

Both are walls by the capture-layer contract (`docs/specs/2026-10-15-capture-layer.md` D4, D6): the capture fails with
`error_class: blocked`, acquisition kind `bot-wall`, not retryable, and the tool's line in the receipt's `error`.
