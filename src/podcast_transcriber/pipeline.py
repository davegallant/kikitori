"""Single-worker audio transcription and crash recovery."""

import json
from pathlib import Path
from queue import Queue
from threading import Lock, Thread
from typing import Callable
from uuid import uuid4

import httpx

from .source import download_audio, resolve_source
from .store import EpisodeStore


def _default_model():
    from faster_whisper import WhisperModel

    return WhisperModel("small", device="cpu", compute_type="int8")


def _serialize_segments(segments, include_words: bool) -> list[dict]:
    result = []
    for segment in segments:
        item = {"start": segment.start, "end": segment.end, "text": segment.text.strip()}
        if include_words:
            item["words"] = [
                {"start": word.start, "end": word.end, "word": word.word}
                for word in (segment.words or [])
            ]
        result.append(item)
    return result


def _timestamp(seconds: float) -> str:
    minutes, remainder = divmod(seconds, 60)
    return f"{int(minutes):02d}:{remainder:05.2f}"


def _write_atomic(path: Path, content: str) -> None:
    temporary = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class TranscriptionWorker:
    def __init__(
        self,
        store: EpisodeStore,
        model_factory: Callable = _default_model,
        resolver: Callable = resolve_source,
        downloader: Callable = download_audio,
        max_bytes: int = 500_000_000,
    ):
        self.store = store
        self.model_factory = model_factory
        self.resolver = resolver
        self.downloader = downloader
        self.max_bytes = max_bytes
        self._model = None
        self._queue: Queue[str | None] = Queue()
        self._pending: set[str] = set()
        self._lock = Lock()
        self._thread: Thread | None = None

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def submit(self, episode_id: str) -> None:
        episode = self.store.get(episode_id)
        if episode is None:
            raise KeyError(f"Episode not found: {episode_id}")
        with self._lock:
            if episode_id in self._pending:
                return
            self._pending.add(episode_id)
        self._queue.put(episode_id)

    def recover(self) -> None:
        for episode in self.store.list():
            if episode.stage not in {"complete", "failed"}:
                self.submit(episode.id)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self.recover()
        self._thread = Thread(target=self._run, daemon=True, name="podcast-transcription")
        self._thread.start()

    def stop(self) -> None:
        if self._thread and self._thread.is_alive():
            self._queue.put(None)
            self._thread.join(timeout=1)

    def _run(self) -> None:
        while True:
            episode_id = self._queue.get()
            if episode_id is None:
                return
            try:
                self.process(episode_id)
            finally:
                with self._lock:
                    self._pending.discard(episode_id)

    def _model_instance(self):
        if self._model is None:
            self._model = self.model_factory()
        return self._model

    def process(self, episode_id: str) -> None:
        episode = self.store.get(episode_id)
        if episode is None:
            return
        try:
            audio_path = None
            if episode.audio_filename:
                candidate = self.store.artifact_path(episode_id, episode.audio_filename)
                if candidate.is_file():
                    audio_path = candidate
            if audio_path is None:
                self.store.update(episode_id, stage="resolving", error=None)
                with httpx.Client(timeout=httpx.Timeout(30, read=120), follow_redirects=False) as client:
                    source = self.resolver(episode.submitted_url, client)
                    self.store.update(
                        episode_id,
                        title=source.title,
                        source_url=source.source_url,
                        audio_url=source.audio_url,
                        source_id=source.source_id,
                        stage="downloading",
                    )
                    audio_path = self.downloader(
                        source,
                        self.store.artifact_path(episode_id, "audio"),
                        client,
                        max_bytes=self.max_bytes,
                    )
                self.store.update(episode_id, audio_filename=audio_path.name)

            self.store.update(episode_id, stage="transcribing", error=None)
            model = self._model_instance()
            japanese, _ = model.transcribe(
                str(audio_path), language="ja", word_timestamps=True, task="transcribe"
            )
            ja_data = _serialize_segments(japanese, include_words=True)
            self.store.update(episode_id, stage="translating")
            english, _ = model.transcribe(str(audio_path), language="ja", task="translate")
            en_data = _serialize_segments(english, include_words=False)

            artifacts = {
                "ja_json_filename": ("transcript_ja.json", json.dumps(ja_data, ensure_ascii=False, indent=2) + "\n"),
                "en_json_filename": ("transcript_en.json", json.dumps(en_data, ensure_ascii=False, indent=2) + "\n"),
                "ja_text_filename": ("transcript_ja.txt", "".join(f"[{_timestamp(item['start'])}] {item['text']}\n" for item in ja_data)),
                "en_text_filename": ("transcript_en.txt", "".join(f"[{_timestamp(item['start'])}] {item['text']}\n" for item in en_data)),
            }
            for filename, content in artifacts.values():
                _write_atomic(self.store.artifact_path(episode_id, filename), content)
            self.store.update(
                episode_id,
                stage="complete",
                error=None,
                **{field: filename for field, (filename, _) in artifacts.items()},
            )
        except Exception as exc:
            self.store.update(episode_id, stage="failed", error=str(exc) or type(exc).__name__)
