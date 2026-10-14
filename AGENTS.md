# Kikitori

Personal, local web app for turning a public Pocket Casts episode page or direct audio URL into a saved Japanese transcript and English translation. The user wants to run it on their own machine and consider production hosting later.

## Resume here

- Run `just dev` from this repository, then open `http://127.0.0.1:8765`. Use `just dev 8766` if that port is occupied. `just` and Nix flakes are required; the recipe enters the Nix shell and `uv` installs the Python app as needed.
- Read `README.md` for user-facing behavior and startup options.
- Work in this repository on `main`. Commit changes as you go with Conventional Commit subjects. Keep commits local until the user explicitly asks to push.

## Architecture

- `src/podcast_transcriber/web.py`: FastAPI routes and CLI; server-rendered templates in `templates/` and small browser assets in `static/`. Binds to `127.0.0.1:8765` by default. The episode page has an audio player, seekable timestamps, Japanese and English text, downloads, progress, manual retry, and a link to the source page.
- `src/podcast_transcriber/source.py`: resolves full Pocket Casts episode URLs through an undocumented Pocket Casts lookup, or accepts direct public HTTP(S) audio URLs. Rejects private/local destinations including redirected or rebound DNS addresses, limits downloads to 500 MB, and verifies decodable audio with PyAV. Keep the safe HTTP client and its pinned `httpcore` dependency together if changing networking.
- `src/podcast_transcriber/pipeline.py`: one background worker, lazy `faster-whisper` small model on CPU with `int8`; Japanese transcription uses `language="ja"` and word timestamps once. `translation.py` sends Japanese passages to GPT-6 Luna through a ChatGPT plan using LiteLLM; each English passage inherits its Japanese passage’s timestamps. Japanese is saved before translation, so retries reuse it. Restart recovery requeues unfinished work and reuses saved transcripts or audio. Completed episodes can rebuild English from saved Japanese through `/episodes/{episode_id}/retranslate`; legacy records have no `translation_method` and show a rebuild notice.
- `src/podcast_transcriber/store.py`: filesystem records under `./data/` by default, one directory per episode. Stores metadata, retained audio, Japanese and English JSON/text transcripts. Search scans saved titles and transcript text; there is no database or separate index service. The data directory is ignored by Git and can be changed with `--data-dir` or `PODCAST_TRANSCRIBER_DATA_DIR`.
- `tests/`: source, store, pipeline, and web coverage. For code changes, the full suite command is `nix develop --command uv run --extra test pytest -q`. A historical full run passed 29 tests; the `just dev` recipe was also launched on port 8766 and served HTTP 200. These are historical results, so verify again when changing behavior.

## Constraints and known limits

- Preserve local-only defaults and the filesystem library. Keep downloaded audio and both transcript formats. Duplicate submissions should redirect to the existing episode.
- Pocket Casts offers no public API; the lookup can change. Direct audio URLs are the fallback. Private feeds and arbitrary podcast page sites are not supported.
- The Pocket Casts metadata response checks a 2 MB limit after reading when `Content-Length` is absent; streaming that response with an in-read cap remains a small hardening task.
- English output may need human correction. Japanese punctuation remains Whisper-generated. Production hosting, accounts, multi-user behavior, database storage, and bulk feed ingestion remain outside the current scope.
