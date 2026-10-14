import io
import socket
import wave
from pathlib import Path

import httpx
import pytest

from podcast_transcriber.source import SourceError, SourceInfo, download_audio, resolve_source, safe_http_client


POCKET_URL = (
    "https://pocketcasts.com/podcast/nihongodekita-podcast/"
    "d11a9ee0-985f-013f-e3f3-0affc88878a7/ep6-%E6%97%A5%E6%9C%AC/"
    "eeb75a75-9d06-4f09-9d81-14733bd76cef"
)
AUDIO_URL = "https://anchor.fm/episode.m4a"
APPLE_URL = "https://podcasts.apple.com/ca/podcast/apple-events/id1473854035?i=1000788729530"


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    original = socket.getaddrinfo

    def fake(host, port, *args, **kwargs):
        if host in {"pocketcasts.com", "podcast-api.pocketcasts.com", "anchor.fm", "example.com",
                    "podcasts.apple.com", "itunes.apple.com"}:
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


def test_apple_podcasts_episode_resolves_publisher_audio_and_title():
    def handler(request):
        assert request.url.host == "itunes.apple.com"
        assert request.url.path == "/lookup"
        assert request.url.params["id"] == "1473854035"
        assert request.url.params["entity"] == "podcastEpisode"
        assert request.url.params["country"] == "ca"
        return httpx.Response(200, json={"resultCount": 2, "results": [
            {"wrapperType": "track", "kind": "podcast", "trackId": 1473854035,
             "trackName": "Apple Events", "feedUrl": "https://feeds.example.com/apple.xml"},
            {"episodeUrl": AUDIO_URL,
             "trackViewUrl": "https://podcasts.apple.com/ca/podcast/apple-event/id1473854035?i=1000788729530",
             "trackName": "Apple Event September 9", "collectionId": 1473854035},
        ]})

    source = resolve_source(APPLE_URL, client_for(handler))
    assert source.source_url == APPLE_URL
    assert source.audio_url == AUDIO_URL
    assert source.title == "Apple Event September 9"
    assert source.source_id == "apple:1473854035:1000788729530"


def test_apple_podcasts_show_link_without_episode_id_is_rejected():
    client = client_for(lambda request: pytest.fail("show-only links need no lookup"))
    with pytest.raises(SourceError, match="episode"):
        resolve_source("https://podcasts.apple.com/us/podcast/apple-events/id1473854035", client)


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


def test_dns_rebinding_cannot_reach_private_address(monkeypatch, tmp_path):
    lookups = []
    connections = []
    original = socket.getaddrinfo

    def changing_dns(host, port, *args, **kwargs):
        if host == "rebind.example":
            lookups.append(host)
            address = "93.184.215.14" if len(lookups) == 1 else "127.0.0.1"
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]
        return original(host, port, *args, **kwargs)

    def record_connection(address, *args, **kwargs):
        connections.append(address)
        raise AssertionError("connection must not be attempted")

    monkeypatch.setattr(socket, "getaddrinfo", changing_dns)
    monkeypatch.setattr(socket, "create_connection", record_connection)
    source = SourceInfo("http://rebind.example/audio", "http://rebind.example/audio", "episode")
    with safe_http_client() as client:
        with pytest.raises(SourceError, match="private|local"):
            download_audio(source, tmp_path / "audio", client, max_bytes=100_000)
    assert len(lookups) >= 2
    assert connections == []


def test_audio_header_without_decodable_frames_is_rejected(tmp_path):
    header_only = wav_bytes()[:44]
    client = client_for(lambda request: httpx.Response(200, content=header_only, headers={"content-type": "audio/wav"}))
    source = SourceInfo("https://example.com/episode.wav", "https://example.com/episode.wav", "episode")
    with pytest.raises(SourceError, match="decodable audio"):
        download_audio(source, tmp_path / "audio", client, max_bytes=100_000)
    assert not list(Path(tmp_path).glob("audio*"))
