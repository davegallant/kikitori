"""Filesystem episode records and simple library search."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from uuid import uuid4


EPISODE_ID = re.compile(r"^[0-9a-f]{32}$")
ARTIFACT_NAME = re.compile(r"^[a-zA-Z0-9_.-]+$")


@dataclass(frozen=True)
class Episode:
    id: str
    submitted_url: str
    source_url: str
    audio_url: str | None
    title: str
    source_id: str | None
    created_at: str
    stage: str
    error: str | None
    audio_filename: str | None
    ja_json_filename: str | None
    en_json_filename: str | None
    ja_text_filename: str | None
    en_text_filename: str | None
    translation_method: str | None = None


class EpisodeStore:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def episode_dir(self, episode_id: str) -> Path:
        if not EPISODE_ID.fullmatch(episode_id):
            raise ValueError("Invalid episode ID")
        return self.data_dir / episode_id

    def _metadata_path(self, episode_id: str) -> Path:
        return self.episode_dir(episode_id) / "metadata.json"

    def _write(self, episode: Episode) -> None:
        path = self._metadata_path(episode.id)
        temporary = path.with_name(f"metadata.{uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(asdict(episode), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def create(self, url: str) -> Episode:
        episode = Episode(
            id=uuid4().hex,
            submitted_url=url,
            source_url=url,
            audio_url=None,
            title="Queued episode",
            source_id=None,
            created_at=datetime.now(timezone.utc).isoformat(),
            stage="queued",
            error=None,
            audio_filename=None,
            ja_json_filename=None,
            en_json_filename=None,
            ja_text_filename=None,
            en_text_filename=None,
        )
        self.episode_dir(episode.id).mkdir()
        self._write(episode)
        return episode

    def get(self, episode_id: str) -> Episode | None:
        if not EPISODE_ID.fullmatch(episode_id):
            return None
        try:
            return Episode(**json.loads(self._metadata_path(episode_id).read_text(encoding="utf-8")))
        except FileNotFoundError:
            return None

    def list(self, query: str = "") -> list[Episode]:
        matches = []
        search = query.strip().casefold()
        for directory in self.data_dir.iterdir():
            if not directory.is_dir() or not EPISODE_ID.fullmatch(directory.name):
                continue
            episode = self.get(directory.name)
            if episode is None:
                continue
            if search:
                content = [episode.title, episode.submitted_url]
                for filename in (episode.ja_text_filename, episode.en_text_filename):
                    if filename:
                        path = self.artifact_path(episode.id, filename)
                        if path.is_file():
                            content.append(path.read_text(encoding="utf-8"))
                if not any(search in item.casefold() for item in content):
                    continue
            matches.append(episode)
        return sorted(matches, key=lambda episode: episode.created_at, reverse=True)

    def matching_passages(self, episode: Episode, query: str, limit: int = 3) -> list[dict]:
        search = query.strip().casefold()
        if not search or limit <= 0:
            return []

        matches = []
        for language, filename in (
            ("Japanese", episode.ja_json_filename),
            ("English", episode.en_json_filename),
        ):
            if not filename:
                continue
            path = self.artifact_path(episode.id, filename)
            if not path.is_file():
                continue
            try:
                passages = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(passages, list):
                continue
            for passage in passages:
                if not isinstance(passage, dict):
                    continue
                text = passage.get("text")
                if isinstance(text, str) and search in text.casefold():
                    matches.append({
                        "language": language,
                        "start": passage.get("start", 0),
                        "text": text,
                    })
        matches.sort(key=lambda passage: float(passage["start"]))
        return matches[:limit]

    def update(self, episode_id: str, **fields) -> Episode:
        episode = self.get(episode_id)
        if episode is None:
            raise KeyError(f"Episode not found: {episode_id}")
        updated = replace(episode, **fields)
        self._write(updated)
        return updated

    def find_duplicate(self, url: str, source_id: str | None = None) -> Episode | None:
        for episode in self.list():
            if episode.submitted_url == url or (source_id and episode.source_id == source_id):
                return episode
        return None

    def artifact_path(self, episode_id: str, filename: str) -> Path:
        if not ARTIFACT_NAME.fullmatch(filename) or filename in {".", ".."}:
            raise ValueError("Invalid artifact name")
        return self.episode_dir(episode_id) / filename
