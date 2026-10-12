import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, BrokenBarrierError

from fastapi.testclient import TestClient

from podcast_transcriber.store import EpisodeStore
from podcast_transcriber.web import create_app


class RecordingWorker:
    def __init__(self):
        self.submitted = []

    def start(self):
        pass

    def stop(self):
        pass

    def submit(self, episode_id):
        self.submitted.append(episode_id)


def test_submitting_a_url_queues_once_and_redirects_duplicate(tmp_path):
    worker = RecordingWorker()
    with TestClient(create_app(tmp_path, worker)) as client:
        first = client.post("/episodes", data={"url": "https://example.com/episode.mp3"}, follow_redirects=False)
        second = client.post("/episodes", data={"url": "https://example.com/episode.mp3"}, follow_redirects=False)
    assert first.status_code == 303
    assert first.headers["location"].startswith("/episodes/")
    assert second.headers["location"] == first.headers["location"]
    assert len(worker.submitted) == 1


def test_library_search_finds_saved_transcript_text(tmp_path):
    store = EpisodeStore(tmp_path)
    episode = store.create("https://example.com/episode.mp3")
    store.update(episode.id, title="Japanese work", en_text_filename="transcript_en.txt")
    store.artifact_path(episode.id, "transcript_en.txt").write_text("A rare phrase", encoding="utf-8")
    with TestClient(create_app(tmp_path, RecordingWorker())) as client:
        found = client.get("/?q=RARE")
        missing = client.get("/?q=absent")
    assert "Japanese work" in found.text
    assert "Japanese work" not in missing.text


def test_episode_page_has_source_link_player_and_transcripts(tmp_path):
    store = EpisodeStore(tmp_path)
    source_url = "https://pocketcasts.com/podcast/show/podcast-id/episode/episode-id"
    episode = store.create(source_url)
    store.artifact_path(episode.id, "audio.m4a").write_bytes(b"audio-bytes")
    store.artifact_path(episode.id, "transcript_ja.json").write_text(json.dumps([
        {"start": 5.5, "end": 7.0, "text": "日本の会社", "words": []}
    ]), encoding="utf-8")
    store.artifact_path(episode.id, "transcript_en.json").write_text(json.dumps([
        {"start": 5.5, "end": 7.0, "text": "Japanese companies"}
    ]), encoding="utf-8")
    store.artifact_path(episode.id, "transcript_ja.txt").write_text("日本の会社", encoding="utf-8")
    store.artifact_path(episode.id, "transcript_en.txt").write_text("Japanese companies", encoding="utf-8")
    store.update(
        episode.id, title="Office life", source_url=source_url, stage="complete",
        audio_filename="audio.m4a", ja_json_filename="transcript_ja.json",
        en_json_filename="transcript_en.json", ja_text_filename="transcript_ja.txt",
        en_text_filename="transcript_en.txt",
    )

    with TestClient(create_app(tmp_path, RecordingWorker())) as client:
        page = client.get(f"/episodes/{episode.id}")
        audio = client.get(f"/episodes/{episode.id}/audio")
        transcript = client.get(f"/episodes/{episode.id}/transcript/ja")
    assert page.status_code == 200
    assert source_url in page.text
    assert "Japanese companies" in page.text
    assert "日本の会社" in page.text
    assert "data-seconds=\"5.5\"" in page.text
    assert f"/episodes/{episode.id}/audio" in page.text
    assert audio.content == b"audio-bytes"
    assert transcript.text == "日本の会社"


def test_bad_id_is_404_and_failed_status_is_visible(tmp_path):
    store = EpisodeStore(tmp_path)
    episode = store.create("https://example.com/bad.mp3")
    store.update(episode.id, stage="failed", error="The download is not valid audio")
    with TestClient(create_app(tmp_path, RecordingWorker())) as client:
        assert client.get("/episodes/../../etc/passwd").status_code == 404
        page = client.get(f"/episodes/{episode.id}")
        status = client.get(f"/episodes/{episode.id}/status")
    assert "The download is not valid audio" in page.text
    assert status.json()["stage"] == "failed"
    assert "valid audio" in status.json()["error"]


def test_local_url_is_rejected_and_remote_title_is_escaped(tmp_path):
    store = EpisodeStore(tmp_path)
    episode = store.create("https://example.com/episode.mp3")
    store.update(episode.id, title="<script>alert(1)</script>")
    with TestClient(create_app(tmp_path, RecordingWorker())) as client:
        rejection = client.post("/episodes", data={"url": "http://127.0.0.1/private"})
        page = client.get(f"/episodes/{episode.id}")
    assert rejection.status_code == 422
    assert "<script>alert(1)</script>" not in page.text
    assert "&lt;script&gt;" in page.text


def test_failed_episode_can_be_retried_manually(tmp_path):
    store = EpisodeStore(tmp_path)
    episode = store.create("https://example.com/episode.mp3")
    store.update(episode.id, stage="failed", error="Temporary download failure")
    worker = RecordingWorker()
    with TestClient(create_app(tmp_path, worker)) as client:
        failed_page = client.get(f"/episodes/{episode.id}")
        retry = client.post(f"/episodes/{episode.id}/retry", follow_redirects=False)
    assert "Retry episode" in failed_page.text
    assert retry.status_code == 303
    assert store.get(episode.id).stage == "queued"
    assert store.get(episode.id).error is None
    assert worker.submitted == [episode.id]


def test_concurrent_submissions_create_only_one_episode(tmp_path, monkeypatch):
    original = EpisodeStore.find_duplicate
    barrier = Barrier(2)

    def slow_lookup(self, url, source_id=None):
        result = original(self, url, source_id)
        if result is None:
            try:
                barrier.wait(timeout=0.3)
            except BrokenBarrierError:
                pass
        return result

    monkeypatch.setattr(EpisodeStore, "find_duplicate", slow_lookup)
    worker = RecordingWorker()
    with TestClient(create_app(tmp_path, worker)) as client:
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(
                lambda _: client.post("/episodes", data={"url": "https://example.com/episode.mp3"}, follow_redirects=False),
                range(2),
            ))
    assert responses[0].headers["location"] == responses[1].headers["location"]
    assert len(EpisodeStore(tmp_path).list()) == 1
    assert len(worker.submitted) == 1
