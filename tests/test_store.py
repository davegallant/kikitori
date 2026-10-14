import json

from podcast_transcriber.store import EpisodeStore


def test_episode_survives_fresh_store_instance(tmp_path):
    first = EpisodeStore(tmp_path)
    episode = first.create("https://example.com/episode.mp3")
    first.update(episode.id, title="Japanese office life", stage="complete", audio_filename="audio.mp3")

    restored = EpisodeStore(tmp_path).get(episode.id)
    assert restored.title == "Japanese office life"
    assert restored.stage == "complete"
    assert restored.audio_filename == "audio.mp3"
    assert restored.submitted_url == "https://example.com/episode.mp3"


def test_metadata_update_is_complete_json_and_leaves_no_temporary_file(tmp_path):
    store = EpisodeStore(tmp_path)
    episode = store.create("https://example.com/a.mp3")
    store.update(episode.id, title="Finished", stage="complete")
    data = json.loads((tmp_path / episode.id / "metadata.json").read_text())
    assert data["title"] == "Finished"
    assert data["stage"] == "complete"
    assert not list((tmp_path / episode.id).glob("*.tmp"))


def test_duplicate_detection_uses_original_url_and_episode_id(tmp_path):
    store = EpisodeStore(tmp_path)
    episode = store.create("https://pocketcasts.com/first")
    store.update(episode.id, source_id="episode-123")
    assert store.find_duplicate("https://pocketcasts.com/first") == store.get(episode.id)
    assert store.find_duplicate("https://pocketcasts.com/another", "episode-123") == store.get(episode.id)
    assert store.find_duplicate("https://pocketcasts.com/other") is None


def test_search_reads_saved_japanese_and_english_files_after_restart(tmp_path):
    store = EpisodeStore(tmp_path)
    episode = store.create("https://example.com/episode.mp3")
    store.update(episode.id, title="Workplace", ja_text_filename="transcript_ja.txt", en_text_filename="transcript_en.txt")
    store.artifact_path(episode.id, "transcript_ja.txt").write_text("日本の会社で働く", encoding="utf-8")
    store.artifact_path(episode.id, "transcript_en.txt").write_text("Japanese companies", encoding="utf-8")

    reloaded = EpisodeStore(tmp_path)
    assert [item.id for item in reloaded.list("会社")] == [episode.id]
    assert [item.id for item in reloaded.list("COMPANIES")] == [episode.id]
    assert [item.id for item in reloaded.list("workPLACE")] == [episode.id]
    assert reloaded.list("missing") == []


def test_invalid_id_cannot_escape_data_directory(tmp_path):
    store = EpisodeStore(tmp_path)
    assert store.get("../../etc/passwd") is None
    try:
        store.artifact_path("../../etc/passwd", "audio.mp3")
    except ValueError:
        pass
    else:
        raise AssertionError("invalid episode ID was accepted")
