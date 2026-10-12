# Podcast Transcriber

A local web library for Japanese podcast episodes. Paste a Pocket Casts episode page or a direct audio URL, then listen and read timestamped Japanese and English transcripts.

## Start

On a machine with Nix flakes and `just` installed, run from this directory:

```sh
just dev
```

Open [http://127.0.0.1:8765](http://127.0.0.1:8765). The first transcription downloads the `faster-whisper` small model, then processes the episode on CPU. A 27-minute episode can take several minutes because Japanese transcription and English translation are separate passes. The page shows the current stage and updates when processing finishes.

The app binds only to `127.0.0.1` by default. Use `just dev 9000` to choose another port. To move the library, run `nix develop --command uv run podcast-transcriber --data-dir /path/to/library`. By default, audio and transcripts are stored in `./data/`, one directory per episode. Keep that directory to preserve the library across restarts.

## Supported links

- Full public Pocket Casts episode pages, such as `https://pocketcasts.com/podcast/.../.../.../...`.
- Direct public HTTP(S) audio URLs, including redirects from podcast hosts.

Pocket Casts [does not offer a public API](https://support.pocketcasts.com/knowledge-base/pocket-casts-api/). This app uses a currently available episode lookup to resolve a Pocket Casts page to its original audio URL. If Pocket Casts changes that lookup or an older episode is not returned, use the direct audio URL instead. Private feeds and arbitrary podcast page sites are not supported yet.

## Files and search

Each episode keeps `metadata.json`, the downloaded audio, `transcript_ja.json` with word timestamps, `transcript_en.json`, and readable `transcript_ja.txt` and `transcript_en.txt`. The library searches saved titles and both text transcripts directly from files. There is no database or separate search service.

The player and timestamps on an episode page let you jump into the audio. The page links back to its original source. English text is Whisper's automatic translation and may need human correction, especially for idioms.

## Development

```sh
nix develop --command uv run --extra test pytest -q
```
