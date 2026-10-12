import io
import socket
import wave
from pathlib import Path

import httpx
import pytest

from podcast_transcriber.source import SourceError, SourceInfo, download_audio, resolve_source


POCKET_URL = (
    "https://pocketcasts.com/podcast/nihongodekita-podcast/"
    "d11a9ee0-985f-013f-e3f3-0affc88878a7/ep6-%E6%97%A5%E6%9C%AC/"
    "eeb75a75-9d06-4f09-9d81-14733bd76cef"
)
AUDIO_URL = "https://anchor.fm/episode.m4a"


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    original = socket.getaddrinfo

    def fake(host, port, *args, **kwargs):
        if host in {"pocketcasts.com", "podcast-api.pocketcasts.com", "anchor.fm", "example.com"}:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", port))]
        return original(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", fake)


def client_for(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


def wav_bytes():
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\0\0" * 1600)
    return output.getvalue()


def test_pocketcasts_page_resolves_its_episode_audio_and_title():
    def handler(request):
        assert request.url.path == "/mobile/show_notes/full/d11a9ee0-985f-013f-e3f3-0affc88878a7"
        return httpx.Response(200, json={"podcast": {"episodes": [
            {"uuid": "eeb75a75-9d06-4f09-9d81-14733bd76cef", "title": "Ep.6 日本の会社", "url": AUDIO_URL}
        ]}})

    source = resolve_source(POCKET_URL, client_for(handler))
    assert source.audio_url == AUDIO_URL
    assert source.title == "Ep.6 日本の会社"
    assert source.source_url == POCKET_URL
    assert source.source_id == "eeb75a75-9d06-4f09-9d81-14733bd76cef"


def test_missing_pocketcasts_episode_reports_error():
    client = client_for(lambda request: httpx.Response(200, json={"podcast": {"episodes": []}}))
    with pytest.raises(SourceError, match="episode"):
        resolve_source(POCKET_URL, client)


def test_direct_audio_url_is_accepted():
    source = resolve_source("https://example.com/audio/episode.mp3", client_for(lambda request: None))
    assert source.audio_url == "https://example.com/audio/episode.mp3"
    assert source.title == "episode.mp3"


@pytest.mark.parametrize("url", ["file:///etc/passwd", "http://127.0.0.1/audio.mp3", "http://localhost/audio.mp3"])
def test_invalid_or_local_source_url_is_rejected(url):
    with pytest.raises(SourceError):
        resolve_source(url, client_for(lambda request: None))


def test_redirect_to_private_address_is_rejected_before_request(tmp_path):
    visited = []

    def handler(request):
        visited.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://127.0.0.1/secret"})

    source = SourceInfo("https://example.com/episode", "https://example.com/episode", "episode")
    with pytest.raises(SourceError, match="private|local"):
        download_audio(source, tmp_path / "audio", client_for(handler), max_bytes=100_000)
    assert visited == ["https://example.com/episode"]


def test_audio_download_is_verified_and_saved_with_extension(tmp_path):
    client = client_for(lambda request: httpx.Response(200, content=wav_bytes(), headers={"content-type": "audio/wav"}))
    source = SourceInfo("https://example.com/episode", "https://example.com/episode", "episode")
    result = download_audio(source, tmp_path / "audio", client, max_bytes=100_000)
    assert result.name == "audio.wav"
    assert result.read_bytes() == wav_bytes()


def test_oversized_download_is_rejected(tmp_path):
    client = client_for(lambda request: httpx.Response(200, content=wav_bytes()))
    source = SourceInfo("https://example.com/episode", "https://example.com/episode", "episode")
    with pytest.raises(SourceError, match="large"):
        download_audio(source, tmp_path / "audio", client, max_bytes=100)
    assert not list(Path(tmp_path).glob("audio*"))


def test_html_response_is_not_saved_as_audio(tmp_path):
    client = client_for(lambda request: httpx.Response(200, content=b"<html>bad</html>", headers={"content-type": "text/html"}))
    source = SourceInfo("https://example.com/episode", "https://example.com/episode", "episode")
    with pytest.raises(SourceError, match="audio"):
        download_audio(source, tmp_path / "audio", client, max_bytes=100_000)
    assert not list(Path(tmp_path).glob("audio*"))


def test_binary_garbage_reports_invalid_audio(tmp_path):
    client = client_for(lambda request: httpx.Response(200, content=b"not-an-audio-file", headers={"content-type": "application/octet-stream"}))
    source = SourceInfo("https://example.com/episode", "https://example.com/episode", "episode")
    with pytest.raises(SourceError, match="valid audio"):
        download_audio(source, tmp_path / "audio", client, max_bytes=100_000)
    assert not list(Path(tmp_path).glob("audio*"))
