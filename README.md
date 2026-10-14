# Kikitori

<p align="center">
  <img src="src/kikitori/static/kikitori-cat.svg" alt="Cute cat wearing headphones" width="320">
</p>

A local library for Japanese podcast episodes. Add a public Pocket Casts or Apple Podcasts episode, or a direct audio URL, to create timestamped Japanese and English transcripts.

![Kikitori episode page](docs/images/kikitori-episode.png)

## Start

Install [Nix](https://nixos.org/download/) with flakes enabled and [just](https://just.systems/), then run:

```sh
just dev
```

Open [http://127.0.0.1:8765](http://127.0.0.1:8765). On first launch, follow the ChatGPT login prompt in the terminal. Transcription runs locally with Whisper; translation uses your ChatGPT plan through LiteLLM. Audio stays on your machine, while Japanese transcript text is sent for translation. Processing can take several minutes.

The app listens only on your machine by default. Use `just dev 8766` to choose another port.

## Library

Episodes, audio, and transcripts are saved in `./data/`. Keep this folder to preserve your library. To use a different location:

```sh
nix develop --command uv run kikitori --data-dir /path/to/library
```

The episode page includes playback, seekable timestamps, furigana, transcript downloads, and a link to the source. Search covers saved episode titles and transcript text. English translations can be rebuilt from the saved Japanese transcript without transcribing the audio again.

## Development

Run the test suite with:

```sh
nix develop --command uv run --extra test pytest -q
```
