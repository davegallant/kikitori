"""Local podcast library web server."""

import argparse
from contextlib import asynccontextmanager
import ipaddress
import json
import mimetypes
import os
from pathlib import Path
from itertools import zip_longest
from threading import Lock
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import uvicorn
import pykakasi

from .pipeline import TranscriptionWorker
from .source import APPLE_HOSTS, APPLE_SHOW_ID, POCKET_PATH
from .store import EpisodeStore
from .translation import ensure_chatgpt_login


PACKAGE_DIR = Path(__file__).parent
_KAKASI = pykakasi.kakasi()


def _furigana_parts(text: str) -> list[dict[str, str | None]]:
    return [
        {"text": item["orig"], "reading": item["hira"] if item["orig"] != item["hira"] else None}
        for item in _KAKASI.convert(text)
    ]


def _check_submission_url(url: str) -> str:
    url = url.strip()
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Paste a public HTTP or HTTPS episode URL")
    if parsed.username or parsed.password or parsed.hostname == "localhost" or parsed.hostname.endswith(".localhost"):
        raise ValueError("Local URLs and URLs with credentials are not supported")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        pass
    else:
        if not address.is_global:
            raise ValueError("Local or private network URLs are not supported")
    return url


def _source_id(url: str) -> str | None:
    parsed = urlsplit(url)
    if parsed.hostname and parsed.hostname.lower() in APPLE_HOSTS:
        show_match = APPLE_SHOW_ID.search(parsed.path)
        episode_ids = parse_qs(parsed.query).get("i", [])
        if show_match and len(episode_ids) == 1 and episode_ids[0].isdigit():
            return f"apple:{show_match.group(1)}:{episode_ids[0]}"
        return None
    if parsed.hostname not in {"pocketcasts.com", "www.pocketcasts.com"}:
        return None
    match = POCKET_PATH.fullmatch(parsed.path)
    return match.group(2).lower() if match else None


def _clock(seconds: float) -> str:
    minutes, remainder = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{remainder:02d}" if hours else f"{minutes}:{remainder:02d}"


def create_app(data_dir: Path, worker: TranscriptionWorker | None = None) -> FastAPI:
    store = EpisodeStore(data_dir)
    worker = worker or TranscriptionWorker(store)
    submission_lock = Lock()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        worker.start()
        try:
            yield
        finally:
            worker.stop()

    app = FastAPI(title="Kikitori", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")
    templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")
    templates.env.filters["clock"] = _clock

    def render_index(request: Request, query: str = "", error: str | None = None, status_code: int = 200):
        return templates.TemplateResponse(
            request,
            "index.html",
            {"episodes": store.list(query), "query": query, "error": error},
            status_code=status_code,
        )

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request, q: str = ""):
        return render_index(request, q)

    @app.post("/episodes")
    def submit(request: Request, url: str = Form(...)):
        try:
            url = _check_submission_url(url)
        except ValueError as exc:
            return render_index(request, error=str(exc), status_code=422)
        source_id = _source_id(url)
        with submission_lock:
            existing = store.find_duplicate(url, source_id)
            if existing:
                return RedirectResponse(f"/episodes/{existing.id}", status_code=303)
            episode = store.create(url)
            if source_id:
                store.update(episode.id, source_id=source_id)
            worker.submit(episode.id)
        return RedirectResponse(f"/episodes/{episode.id}", status_code=303)

    def required_episode(episode_id: str):
        episode = store.get(episode_id)
        if episode is None:
            raise HTTPException(404, "Episode not found")
        return episode

    @app.get("/episodes/{episode_id}", response_class=HTMLResponse)
    def detail(request: Request, episode_id: str):
        episode = required_episode(episode_id)
        japanese = []
        english = []
        transcript_pairs = []
        if episode.stage == "complete":
            for filename, target in ((episode.ja_json_filename, japanese), (episode.en_json_filename, english)):
                if filename:
                    path = store.artifact_path(episode_id, filename)
                    if path.is_file():
                        target.extend(json.loads(path.read_text(encoding="utf-8")))
            for passage in japanese:
                passage["furigana_parts"] = _furigana_parts(passage["text"])
            transcript_pairs = list(zip_longest(japanese, english))
        return templates.TemplateResponse(
            request,
            "episode.html",
            {"episode": episode, "japanese": japanese, "english": english, "transcript_pairs": transcript_pairs},
        )

    @app.get("/episodes/{episode_id}/status")
    def status(episode_id: str):
        episode = required_episode(episode_id)
        return {"stage": episode.stage, "error": episode.error, "title": episode.title}

    @app.post("/episodes/{episode_id}/retry")
    def retry(episode_id: str):
        episode = required_episode(episode_id)
        if episode.stage != "failed":
            raise HTTPException(409, "Only failed episodes can be retried")
        store.update(episode_id, stage="queued", error=None)
        worker.submit(episode_id)
        return RedirectResponse(f"/episodes/{episode_id}", status_code=303)

    @app.post("/episodes/{episode_id}/retranslate")
    def retranslate(episode_id: str):
        with submission_lock:
            episode = required_episode(episode_id)
            if episode.stage != "complete":
                raise HTTPException(409, "Only completed episodes can be retranslated")
            if (not episode.ja_json_filename or not
                    store.artifact_path(episode_id, episode.ja_json_filename).is_file()):
                raise HTTPException(409, "Saved Japanese transcript is required")
            store.update(episode_id, stage="queued", error=None)
            worker.submit(episode_id)
        return RedirectResponse(f"/episodes/{episode_id}", status_code=303)

    @app.get("/episodes/{episode_id}/audio")
    def audio(episode_id: str):
        episode = required_episode(episode_id)
        if not episode.audio_filename:
            raise HTTPException(404, "Audio is not available yet")
        path = store.artifact_path(episode_id, episode.audio_filename)
        if not path.is_file():
            raise HTTPException(404, "Audio file is missing")
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return FileResponse(path, media_type=media_type)

    @app.get("/episodes/{episode_id}/transcript/{language}")
    def transcript(episode_id: str, language: str):
        episode = required_episode(episode_id)
        filename = {"ja": episode.ja_text_filename, "en": episode.en_text_filename}.get(language)
        if episode.stage != "complete" or not filename:
            raise HTTPException(404, "Transcript is not available")
        path = store.artifact_path(episode_id, filename)
        if not path.is_file():
            raise HTTPException(404, "Transcript file is missing")
        return FileResponse(path, media_type="text/plain; charset=utf-8", filename=filename)

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Kikitori, the local podcast transcriber")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--data-dir", type=Path, default=Path(os.environ.get("PODCAST_TRANSCRIBER_DATA_DIR", "data"))
    )
    args = parser.parse_args()
    ensure_chatgpt_login()
    uvicorn.run(create_app(args.data_dir), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
