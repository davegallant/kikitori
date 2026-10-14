"""Podcast source resolution and verified audio downloads."""

from contextlib import contextmanager
from dataclasses import dataclass
import ipaddress
from pathlib import Path
import re
import socket
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlsplit

import av
import httpcore
import httpx


class SourceError(ValueError):
    """A submitted source could not be resolved to playable audio."""


@dataclass(frozen=True)
class SourceInfo:
    source_url: str
    audio_url: str
    title: str
    source_id: str | None = None


POCKET_PATH = re.compile(
    r"^/podcast/[^/]+/([0-9a-f-]{36})/[^/]+/([0-9a-f-]{36})/?$",
    re.IGNORECASE,
)
APPLE_HOSTS = {"podcasts.apple.com", "itunes.apple.com"}
APPLE_SHOW_ID = re.compile(r"(?:^|/)id(\d+)(?:/|$)", re.IGNORECASE)
REDIRECT_CODES = {301, 302, 303, 307, 308}
MAX_METADATA_BYTES = 2_000_000


class _PublicNetworkBackend(httpcore.SyncBackend):
    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        try:
            addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise SourceError(f"Could not resolve host: {host}") from exc
        candidates = [ipaddress.ip_address(item[4][0]) for item in addresses]
        if not candidates or any(not address.is_global for address in candidates):
            raise SourceError("Local or private network URLs are not supported")
        # Connect to the checked IP. HTTPX keeps the original host for HTTP Host and TLS SNI.
        return super().connect_tcp(
            str(candidates[0]), port, timeout=timeout,
            local_address=local_address, socket_options=socket_options,
        )


class _PublicAddressTransport(httpx.HTTPTransport):
    def __init__(self):
        super().__init__(trust_env=False)
        # HTTPX does not expose network_backend; pin its locked HTTP Core version.
        self._pool._network_backend = _PublicNetworkBackend()


def safe_http_client() -> httpx.Client:
    """Create a client for untrusted episode URLs."""
    return httpx.Client(
        transport=_PublicAddressTransport(), trust_env=False,
        follow_redirects=False, timeout=httpx.Timeout(30, read=120),
    )


def _validate_public_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise SourceError("Only public HTTP(S) URLs are supported")
    if parsed.username or parsed.password:
        raise SourceError("URLs with embedded credentials are not supported")
    host = parsed.hostname
    if host.lower() == "localhost" or host.lower().endswith(".localhost"):
        raise SourceError("Local or private network URLs are not supported")
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            addresses = [ipaddress.ip_address(item[4][0]) for item in socket.getaddrinfo(host, parsed.port or 443)]
        except (OSError, ValueError) as exc:
            raise SourceError(f"Could not resolve host: {host}") from exc
    if not addresses or any(not address.is_global for address in addresses):
        raise SourceError("Local or private network URLs are not supported")


@contextmanager
def _safe_stream(client: httpx.Client, url: str):
    current = url
    try:
        for _ in range(6):
            _validate_public_url(current)
            with client.stream("GET", current, follow_redirects=False) as response:
                if response.status_code in REDIRECT_CODES:
                    location = response.headers.get("location")
                    if not location:
                        raise SourceError("The source redirected without a destination")
                    current = urljoin(current, location)
                    continue
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    raise SourceError(f"The source returned HTTP {response.status_code}") from exc
                yield response
                return
        raise SourceError("The source redirected too many times")
    except httpx.HTTPError as exc:
        raise SourceError(f"Could not fetch the source: {exc}") from exc


def _read_metadata(response: httpx.Response, source_name: str) -> bytes:
    if int(response.headers.get("content-length", "0")) > MAX_METADATA_BYTES:
        raise SourceError(f"{source_name} metadata is too large")
    body = bytearray()
    for chunk in response.iter_bytes():
        if len(body) + len(chunk) > MAX_METADATA_BYTES:
            raise SourceError(f"{source_name} metadata is too large")
        body.extend(chunk)
    return bytes(body)


def _apple_episode_id(item: dict) -> str | None:
    track_id = item.get("trackId")
    if isinstance(track_id, (int, str)):
        return str(track_id)
    track_view_url = item.get("trackViewUrl")
    if isinstance(track_view_url, str) and track_view_url:
        return parse_qs(urlsplit(track_view_url).query).get("i", [None])[0]
    return None


def resolve_source(url: str, client: httpx.Client) -> SourceInfo:
    url = url.strip()
    _validate_public_url(url)
    parsed = urlsplit(url)
    if parsed.hostname and parsed.hostname.lower() in APPLE_HOSTS:
        show_match = APPLE_SHOW_ID.search(parsed.path)
        episode_ids = parse_qs(parsed.query).get("i", [])
        if not show_match or len(episode_ids) != 1 or not episode_ids[0].isdigit():
            raise SourceError("Expected an Apple Podcasts episode URL with a show ID and episode ID")
        show_id = show_match.group(1)
        episode_id = episode_ids[0]
        path_parts = parsed.path.strip("/").split("/")
        country = path_parts[0].lower() if path_parts and re.fullmatch(r"[a-z]{2}", path_parts[0], re.IGNORECASE) else "us"
        lookup_url = "https://itunes.apple.com/lookup?" + urlencode({
            "id": show_id, "entity": "podcastEpisode", "limit": 200, "country": country,
        })
        with _safe_stream(client, lookup_url) as response:
            body = _read_metadata(response, "Apple Podcasts")
        try:
            results = httpx.Response(200, content=body).json()["results"]
            episode = next(
                item for item in results
                if isinstance(item, dict) and _apple_episode_id(item) == episode_id
            )
        except (KeyError, StopIteration, TypeError, ValueError) as exc:
            raise SourceError("Could not find this episode in Apple Podcasts metadata") from exc
        audio_url = episode.get("episodeUrl")
        if not isinstance(audio_url, str) or not audio_url:
            raise SourceError("Apple Podcasts did not provide public audio for this episode")
        title = episode.get("trackName")
        if not isinstance(title, str) or not title:
            title = "Untitled episode"
        _validate_public_url(audio_url)
        return SourceInfo(url, audio_url, title, f"apple:{show_id}:{episode_id}")
    if parsed.hostname in {"pocketcasts.com", "www.pocketcasts.com"}:
        match = POCKET_PATH.fullmatch(parsed.path)
        if not match:
            raise SourceError("Expected a Pocket Casts episode page URL")
        podcast_id, episode_id = match.groups()
        lookup_url = f"https://podcast-api.pocketcasts.com/mobile/show_notes/full/{podcast_id}"
        with _safe_stream(client, lookup_url) as response:
            body = _read_metadata(response, "Pocket Casts")
        try:
            episodes = httpx.Response(200, content=body).json()["podcast"]["episodes"]
            episode = next(item for item in episodes if item["uuid"].lower() == episode_id.lower())
            audio_url = episode["url"]
            title = episode["title"]
        except (KeyError, StopIteration, TypeError, ValueError) as exc:
            raise SourceError("Could not find this episode in Pocket Casts metadata") from exc
        _validate_public_url(audio_url)
        return SourceInfo(url, audio_url, title, episode_id.lower())
    title = unquote(Path(parsed.path).name) or "Untitled audio"
    return SourceInfo(url, url, title)


def _audio_extension(path: Path) -> str:
    try:
        with av.open(str(path), mode="r", metadata_errors="ignore") as container:
            if not container.streams.audio:
                raise SourceError("The download does not contain audio")
            container_name = container.format.name
            has_frames = False
            for frame in container.decode(audio=0):
                has_frames = has_frames or frame.samples > 0
            if not has_frames:
                raise SourceError("The download does not contain decodable audio")
    except (av.error.FFmpegError, ValueError, OSError) as exc:
        if isinstance(exc, SourceError):
            raise
        raise SourceError("The download is not valid audio") from exc
    if "mp3" in container_name:
        return ".mp3"
    if "wav" in container_name:
        return ".wav"
    if "flac" in container_name:
        return ".flac"
    if "ogg" in container_name:
        return ".ogg"
    if "webm" in container_name:
        return ".webm"
    if any(part in container_name for part in ("mov", "mp4", "m4a")):
        return ".m4a"
    return ".audio"


def download_audio(source: SourceInfo, destination: Path, client: httpx.Client, max_bytes: int) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".part")
    try:
        with _safe_stream(client, source.audio_url) as response:
            if response.headers.get("content-type", "").lower().startswith("text/html"):
                raise SourceError("The URL returned a web page instead of audio")
            if int(response.headers.get("content-length", "0")) > max_bytes:
                raise SourceError("The audio file is too large")
            size = 0
            with temporary.open("wb") as output:
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > max_bytes:
                        raise SourceError("The audio file is too large")
                    output.write(chunk)
        suffix = _audio_extension(temporary)
        target = destination.with_suffix(suffix)
        temporary.replace(target)
        return target
    finally:
        temporary.unlink(missing_ok=True)
