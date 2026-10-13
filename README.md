# Podcast Transcriber

A local web library for Japanese podcast episodes. Paste a Pocket Casts episode page or a direct audio URL, then listen and read timestamped Japanese and English transcripts.

## Start

On a machine with Nix flakes and `just` installed, run from this directory:

```sh
just dev
```

Open [http://127.0.0.1:8765](http://127.0.0.1:8765). The first episode downloads the `faster-whisper` small speech model and the [OPUS-MT Japanese-to-English text model](https://huggingface.co/Helsinki-NLP/opus-mt-ja-en), then processes locally on CPU. Whisper transcribes the audio once. Each Japanese passage is then translated into English with the same timestamps. No API key is needed and transcript text stays on your machine. A long episode can still take several minutes. The page shows the current stage and updates when processing finishes.

The app binds only to `127.0.0.1` by default. Use `just dev 9000` to choose another port. To move the library, run `nix develop --command uv run podcast-transcriber --data-dir /path/to/library`. By default, audio and transcripts are stored in `./data/`, one directory per episode. Keep that directory to preserve the library across restarts.

## Supported links

- Full public Pocket Casts episode pages, such as `https://pocketcasts.com/podcast/.../.../.../...`.
- Direct public HTTP(S) audio URLs, including redirects from podcast hosts.

Pocket Casts [does not offer a public API](https://support.pocketcasts.com/knowledge-base/pocket-casts-api/). This app uses a currently available episode lookup to resolve a Pocket Casts page to its original audio URL. If Pocket Casts changes that lookup or an older episode is not returned, use the direct audio URL instead. Private feeds and arbitrary podcast page sites are not supported yet.

## Files and search

Each episode keeps `metadata.json`, the downloaded audio, `transcript_ja.json` with word timestamps, `transcript_en.json`, and readable `transcript_ja.txt` and `transcript_en.txt`. The library searches saved titles and both text transcripts directly from files. There is no database or separate search service.

The player and timestamps on an episode page let you jump into the audio. The page links back to its original source. English text is generated from the Japanese transcript by OPUS-MT and may need human correction, especially for idioms or transcription errors. Punctuation comes from Whisper and can still be imperfect; rebuilding English does not change the Japanese wording.

For episodes saved with the old two-pass pipeline, use **Rebuild English translation** on the episode page to fix passage alignment. This reuses the saved Japanese transcript without downloading or transcribing the audio again. New jobs also save Japanese before translation, so failed translations can be retried without repeating Whisper. The first translation downloads its model; later runs use the local cache.

## Development

```sh
nix develop --command uv run --extra test pytest -q
```
