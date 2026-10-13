import json
from pathlib import Path
from types import SimpleNamespace
import time

from podcast_transcriber.pipeline import TranscriptionWorker
from podcast_transcriber.source import SourceInfo
from podcast_transcriber.store import EpisodeStore


class FakeModel:
    def __init__(self):
        self.calls = []

    def transcribe(self, audio, **options):
        self.calls.append(options)
        if options.get("task") == "translate":
            return iter([SimpleNamespace(start=1.0, end=2.0, text=" Japanese companies.")]), None
        word = SimpleNamespace(start=1.0, end=1.5, word="日本の")
        segment = SimpleNamespace(start=1.0, end=2.0, text=" 日本の会社。", words=[word])
        return iter([segment]), SimpleNamespace(language="ja")


def fake_resolve(url, client):
    return SourceInfo(url, "https://example.com/audio.m4a", "Episode six", "episode-6")


def fake_download(source, destination, client, max_bytes):
    path = destination.with_suffix(".m4a")
    path.write_bytes(b"fake-audio")
    return path


def make_worker(store, model, **options):
    return TranscriptionWorker(
        store,
        model_factory=lambda: model,
        resolver=options.get("resolver", fake_resolve),
        downloader=options.get("downloader", fake_download),
        translator_factory=lambda: options.get("translator", lambda text: "Japanese companies."),
    )


def test_pipeline_writes_bilingual_artifacts_and_word_timestamps(tmp_path):
    store = EpisodeStore(tmp_path)
    episode = store.create("https://pocketcasts.com/episode")
    stages = []
    original_update = store.update

    def recording_update(episode_id, **fields):
        if "stage" in fields:
            stages.append(fields["stage"])
        return original_update(episode_id, **fields)

    store.update = recording_update
    model = FakeModel()
    make_worker(store, model).process(episode.id)

    saved = store.get(episode.id)
    assert saved.stage == "complete"
    assert stages == ["resolving", "downloading", "transcribing", "translating", "complete"]
    assert saved.title == "Episode six"
    assert saved.audio_filename == "audio.m4a"
    ja = json.loads(store.artifact_path(episode.id, saved.ja_json_filename).read_text())
    en = json.loads(store.artifact_path(episode.id, saved.en_json_filename).read_text())
    assert ja[0]["words"] == [{"start": 1.0, "end": 1.5, "word": "日本の"}]
    assert en[0]["text"] == "Japanese companies."
    assert "日本の会社。" in store.artifact_path(episode.id, saved.ja_text_filename).read_text()
    assert "Japanese companies." in store.artifact_path(episode.id, saved.en_text_filename).read_text()
    assert model.calls == [
        {"language": "ja", "word_timestamps": True, "task": "transcribe"},
    ]


def test_failed_job_is_visible_and_does_not_mark_complete(tmp_path):
    store = EpisodeStore(tmp_path)
    episode = store.create("https://example.com/bad")

    def fail_resolve(url, client):
        raise ValueError("No episode audio found")

    make_worker(store, FakeModel(), resolver=fail_resolve).process(episode.id)
    saved = store.get(episode.id)
    assert saved.stage == "failed"
    assert "No episode audio found" in saved.error
    assert saved.ja_json_filename is None


def test_restart_recovers_interrupted_job_from_saved_audio(tmp_path):
    store = EpisodeStore(tmp_path)
    episode = store.create("https://example.com/episode")
    store.artifact_path(episode.id, "audio.m4a").write_bytes(b"saved-audio")
    store.update(episode.id, stage="transcribing", audio_filename="audio.m4a")

    def should_not_resolve(url, client):
        raise AssertionError("saved audio should be reused")

    worker = make_worker(store, FakeModel(), resolver=should_not_resolve)
    worker.start()
    try:
        deadline = time.monotonic() + 3
        while store.get(episode.id).stage != "complete" and time.monotonic() < deadline:
            time.sleep(0.01)
        assert store.get(episode.id).stage == "complete"
    finally:
        worker.stop()


def test_failed_jobs_are_not_automatically_retried_on_restart(tmp_path):
    store = EpisodeStore(tmp_path)
    episode = store.create("https://example.com/bad")
    store.update(episode.id, stage="failed", error="bad audio")
    worker = make_worker(store, FakeModel())
    worker.recover()
    assert worker.pending_count == 0


def test_translation_failure_preserves_japanese_for_retry(tmp_path):
    store = EpisodeStore(tmp_path)
    episode = store.create("https://example.com/episode")
    model = FakeModel()

    def fail_translation(text):
        raise RuntimeError("Translation unavailable")

    make_worker(store, model, translator=fail_translation).process(episode.id)
    failed = store.get(episode.id)
    assert failed.stage == "failed"
    assert failed.ja_json_filename
    original = store.artifact_path(episode.id, failed.ja_json_filename).read_bytes()
    make_worker(store, model).process(episode.id)
    saved = store.get(episode.id)
    assert saved.stage == "complete"
    assert len(model.calls) == 1
    assert store.artifact_path(episode.id, saved.ja_json_filename).read_bytes() == original


def test_saved_japanese_is_translated_passage_by_passage_without_audio(tmp_path):
    store = EpisodeStore(tmp_path)
    episode = store.create("https://example.com/episode")
    japanese = [
        {"start": 0.0, "end": 3.1, "text": "こんにちは。", "words": []},
        {"start": 3.1, "end": 9.0, "text": "今日は雨です。", "words": []},
    ]
    store.artifact_path(episode.id, "transcript_ja.json").write_text(json.dumps(japanese))
    store.update(episode.id, ja_json_filename="transcript_ja.json")
    model = FakeModel()
    translations = {"こんにちは。": "Hello.", "今日は雨です。": "It is raining today."}
    def no_download(*args):
        raise AssertionError("Translation must reuse saved Japanese")
    make_worker(store, model, resolver=no_download, translator=translations.__getitem__).process(episode.id)
    saved = store.get(episode.id)
    assert saved.stage == "complete"
    assert model.calls == []
    english = json.loads(store.artifact_path(episode.id, saved.en_json_filename).read_text())
    assert english == [
        {"start": segment["start"], "end": segment["end"], "text": translations[segment["text"]]}
        for segment in japanese
    ]
    assert saved.translation_method == "opus-mt-ja-en-v1"
