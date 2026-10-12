# Local Podcast Transcriber — Design

## Intent

Make the Japanese podcast transcription workflow repeatable from a local web page. The first useful case is pasting a Pocket Casts episode page, then returning later to listen, read the Japanese transcript and English translation, and find that episode again. This is a personal app running on one machine; deployment and multi-user access are outside this version.

## Proposed scope

- Run a local HTTP server bound to `127.0.0.1`, default port `8765`.
- Accept Pocket Casts episode page links like the [sample episode](https://pocketcasts.com/podcast/nihongodekita-podcast/d11a9ee0-985f-013f-e3f3-0affc88878a7/ep6-%E6%97%A5%E6%9C%AC%E3%81%AE%E4%BC%9A%E7%A4%BE%E3%81%AB%E3%81%AF%E3%82%AF%E3%83%93%E3%81%8C%E3%81%AA%E3%81%84-japanese-companies-dont-fire-people-japanese-listening-n3/eeb75a75-9d06-4f09-9d81-14733bd76cef) and direct HTTP(S) audio URLs. A Pocket Casts page resolves its podcast and episode IDs to the audio URL and title. Unsupported links get a useful error.
- Download and retain the audio. Reject a failed download or a response that cannot be decoded as audio; do not repeatedly retry a bad URL.
- Process one episode at a time in the background with `faster-whisper` small, Japanese language, CPU `int8`, and word timestamps. Run a second Whisper translation pass for English. Display progress stages and actionable failures while the browser remains usable.
- Keep all records on the filesystem, one directory per episode: metadata/status JSON, original audio, structured timestamp data, and exportable Japanese and English text. No database in this version.
- Show a library ordered by submission time with a search box that searches titles and transcript text across the local collection. For a personal library, scanning stored files is sufficient; no separate search index is required yet.
- Show an episode page with the saved audio player, readable Japanese and English transcript sections, timestamps that seek the player, and a link back to the original Pocket Casts page. Keep word timestamps in the structured file even if the reading view groups words into segments.
- Recognize a previously submitted source URL or Pocket Casts episode ID and take the user to its existing record instead of silently duplicating a download.

## Implementation calls

- Use a small Python server (FastAPI plus server-rendered templates and a little browser JavaScript) and a single background worker. This keeps setup and UI dependencies modest while reusing the Python transcription code proven in the manual run.
- Use `uv` for dependency setup and lock compatible versions of PyAV and `faster-whisper`; the manual run needed `av<16` with the installed Python runtime. Document any required Nix runtime libraries and a one-command local start.
- Use the Pocket Casts JSON lookup that was verified for the sample page. It is undocumented: [Pocket Casts says there is no public API](https://support.pocketcasts.com/knowledge-base/pocket-casts-api/). Isolate that lookup in one resolver, report a clear error if it changes, and keep direct audio URL support as a fallback.
- Keep the service local-only by default. Validate submitted URLs and follow redirects with limits; reject local/private network targets, cap download size, and treat remote page data and transcript text as untrusted when rendering HTML.
- Write episode metadata atomically. On restart, mark a job interrupted during processing as ready to retry from its saved audio or as failed with a visible explanation; never present a partial transcript as complete.

## Success criteria

From a fresh checkout, the documented start command opens the local page. Pasting the sample Pocket Casts URL resolves to the audio used in the manual run, completes both small-model passes, and leaves a playable episode with readable bilingual transcripts and a source link. Restarting the server preserves the episode. Searching for a phrase in either transcript finds it. Bad URLs and non-audio responses produce visible errors without trapping the queue.

## Deferred

Accounts, public hosting, database storage, automatic feed subscriptions, bulk imports, editing transcripts, speaker labels, and support promises for arbitrary podcast sites or private feeds.
