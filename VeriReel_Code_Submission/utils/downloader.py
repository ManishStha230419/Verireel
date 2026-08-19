"""Constrained TikTok ingestion for transient video comparisons.

Only HTTPS TikTok URLs are accepted. Downloaded media stays inside the caller's
per-job directory, where the Flask worker deletes it after the comparison.
"""

from __future__ import annotations

import json
import ipaddress
import re
import socket
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit


ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".webm", ".mkv"}


class _SilentYtDlpLogger:
    """Keep handled extractor failures out of the application console."""

    @staticmethod
    def debug(_message: str) -> None:
        return None

    @staticmethod
    def warning(_message: str) -> None:
        return None

    @staticmethod
    def error(_message: str) -> None:
        return None


def validate_tiktok_url(value: object) -> str:
    """Return a normalized TikTok HTTPS URL or raise a user-safe error."""
    url = str(value or "").strip()
    if not url:
        raise ValueError("Both TikTok video links are required.")
    if len(url) > 2048:
        raise ValueError("A TikTok link is too long.")

    parsed = urlsplit(url)
    hostname = (parsed.hostname or "").rstrip(".").lower()
    is_tiktok = hostname == "tiktok.com" or hostname.endswith(".tiktok.com")
    if parsed.scheme.lower() != "https" or not is_tiktok or parsed.username or parsed.password:
        raise ValueError("Use a valid HTTPS link from tiktok.com for each video.")
    if not parsed.path or parsed.path == "/":
        raise ValueError("Use a TikTok post link, not the TikTok home page.")

    # Fragments are never needed by the extractor and may contain tracking data.
    return urlunsplit(("https", hostname, parsed.path, "", ""))


def download_tiktok(
    url: str,
    output_dir: Path,
    slot: str,
    *,
    max_video_mb: int = 200,
    max_duration_seconds: int = 300,
    socket_timeout: int = 30,
) -> tuple[Path, dict[str, Any]]:
    """Download one TikTok video and return its local path and public metadata."""
    normalized_url = validate_tiktok_url(url)
    if slot not in {"video1", "video2"}:
        raise ValueError("Invalid download slot.")

    directory = Path(output_dir).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    max_bytes = max(1, int(max_video_mb)) * 1024 * 1024
    max_duration = max(1, int(max_duration_seconds))

    try:
        import yt_dlp
        from yt_dlp.utils import DownloadError
    except ImportError as exc:  # pragma: no cover - deployment configuration guard
        raise RuntimeError("TikTok link support is not installed on this server.") from exc

    def reject_unsuitable(info: dict[str, Any], *, incomplete: bool) -> str | None:
        if info.get("is_live"):
            return "Live TikTok streams are not supported. Use a published video post."
        duration = info.get("duration")
        if not incomplete and duration and float(duration) > max_duration:
            return f"TikTok videos must be {max_duration} seconds or shorter."
        return None

    def stop_oversized_download(status: dict[str, Any]) -> None:
        downloaded = int(status.get("downloaded_bytes") or 0)
        if downloaded > max_bytes:
            raise DownloadError(f"The TikTok video is larger than {max_video_mb} MB.")

    options = {
        "format": "best[ext=mp4]/best",
        "outtmpl": str(directory / f"{slot}-%(id)s.%(ext)s"),
        # Reuse anonymous challenge cookies between Video 1 and Video 2. The
        # cookie file lives in the transient job directory and is deleted with
        # the downloaded media.
        "cookiefile": str(directory / ".tiktok-session.cookies"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        # The normal extractor can fail before the official-embed fallback
        # succeeds. Route its handled messages to a silent logger so successful
        # jobs do not print misleading ERROR lines in the launcher window.
        "logger": _SilentYtDlpLogger(),
        "restrictfilenames": True,
        "max_filesize": max_bytes,
        "socket_timeout": max(5, int(socket_timeout)),
        "retries": 1,
        "extractor_retries": 1,
        "match_filter": reject_unsuitable,
        "progress_hooks": [stop_oversized_download],
        "overwrites": False,
        "continuedl": False,
    }

    try:
        with yt_dlp.YoutubeDL(options) as downloader:
            info = downloader.extract_info(normalized_url, download=True)
            if not isinstance(info, dict):
                raise RuntimeError("TikTok did not return usable video information.")
            path = _downloaded_path(downloader, info, directory)
    except DownloadError as exc:
        try:
            return _download_from_official_embed(
                normalized_url,
                directory,
                slot,
                max_bytes=max_bytes,
                max_duration=max_duration,
                socket_timeout=max(5, int(socket_timeout)),
            )
        except Exception as fallback_exc:
            message = _safe_download_error(exc)
            fallback_message = str(fallback_exc).strip()
            if fallback_message:
                message = fallback_message
            raise RuntimeError(message[:300]) from exc

    if path.suffix.lower() not in ALLOWED_VIDEO_EXTENSIONS:
        raise RuntimeError("TikTok returned a media format this analyzer cannot read.")
    size = path.stat().st_size
    if size <= 0:
        raise RuntimeError("TikTok returned an empty video file.")
    if size > max_bytes:
        raise RuntimeError(f"The TikTok video is larger than {max_video_mb} MB.")

    extractor = str(info.get("extractor_key") or info.get("extractor") or "").lower()
    if "tiktok" not in extractor:
        raise RuntimeError("The supplied link did not resolve to a TikTok video.")

    upload_date = _display_date(info.get("upload_date"))
    timestamp = float(info.get("timestamp") or 0)
    return path, {
        "title": str(info.get("title") or info.get("description") or f"TikTok {slot}")[:140],
        "filename": path.name,
        "author": str(info.get("uploader") or info.get("creator") or info.get("uploader_id") or "Not available")[:100],
        "platform": "TikTok",
        "upload_date": upload_date,
        "timestamp": timestamp,
        "description": str(info.get("description") or "TikTok video supplied by URL.")[:500],
        "source_url": str(info.get("webpage_url") or normalized_url)[:2048],
        "source_type": "tiktok_url",
        "view_count": _safe_count(info.get("view_count")),
        "like_count": _safe_count(info.get("like_count")),
    }


def _downloaded_path(downloader: Any, info: dict[str, Any], directory: Path) -> Path:
    requested = info.get("requested_downloads") or []
    candidate = requested[0].get("filepath") if requested and isinstance(requested[0], dict) else None
    path = Path(candidate or downloader.prepare_filename(info)).resolve()
    if path.parent != directory or not path.is_file():
        matches = sorted(directory.glob("*"), key=lambda item: item.stat().st_mtime, reverse=True)
        path = next(
            (
                item.resolve()
                for item in matches
                if item.is_file() and item.suffix.lower() in ALLOWED_VIDEO_EXTENSIONS
            ),
            path,
        )
    if path.parent != directory or not path.is_file():
        raise RuntimeError("The downloaded TikTok video could not be located safely.")
    return path


def _download_from_official_embed(
    normalized_url: str,
    directory: Path,
    slot: str,
    *,
    max_bytes: int,
    max_duration: int,
    socket_timeout: int,
) -> tuple[Path, dict[str, Any]]:
    """Use TikTok's public embed data when its normal post page is challenged."""
    match = re.search(r"/video/(\d+)", normalized_url)
    if not match:
        raise RuntimeError("The TikTok link does not contain a video post identifier.")
    video_id = match.group(1)

    try:
        from curl_cffi import requests as curl_requests
    except ImportError as exc:  # pragma: no cover - deployment configuration guard
        raise RuntimeError("TikTok's public embed fallback is not installed on this server.") from exc

    embed_url = f"https://www.tiktok.com/embed/v2/{video_id}"
    response = _request_with_safe_redirects(
        curl_requests,
        embed_url,
        validator=_is_tiktok_page_url,
        impersonate="chrome",
        timeout=socket_timeout,
    )
    _require_successful_tiktok_response(response)
    video_data = _parse_embed_video_data(response.text, video_id)
    item = video_data.get("itemInfos") or {}
    video = item.get("video") or {}
    video_meta = video.get("videoMeta") or {}
    duration = float(video_meta.get("duration") or 0)
    if duration and duration > max_duration:
        raise RuntimeError(f"TikTok videos must be {max_duration} seconds or shorter.")

    media_urls = video.get("urls") or []
    media_url = next((str(value) for value in media_urls if _is_tiktok_cdn_url(value)), "")
    if not media_url:
        raise RuntimeError(
            "TikTok's public embed does not expose a downloadable video for this post. "
            "It may be restricted, private, or unavailable."
        )

    path = (directory / f"{slot}-{video_id}.mp4").resolve()
    if path.parent != directory:
        raise RuntimeError("The TikTok fallback produced an unsafe output path.")
    media_response = _request_with_safe_redirects(
        curl_requests,
        media_url,
        validator=_is_tiktok_cdn_url,
        impersonate="chrome",
        timeout=socket_timeout,
        stream=True,
        headers={"Referer": embed_url},
    )
    _require_successful_tiktok_response(media_response)
    content_length = int(media_response.headers.get("content-length") or 0)
    if content_length > max_bytes:
        raise RuntimeError(f"The TikTok video is larger than {max_bytes // (1024 * 1024)} MB.")

    downloaded = 0
    with path.open("wb") as handle:
        for chunk in media_response.iter_content(chunk_size=256 * 1024):
            if not chunk:
                continue
            downloaded += len(chunk)
            if downloaded > max_bytes:
                raise RuntimeError(f"The TikTok video is larger than {max_bytes // (1024 * 1024)} MB.")
            handle.write(chunk)
    if downloaded <= 0:
        raise RuntimeError("TikTok returned an empty video file.")

    author_data = video_data.get("authorInfos") or {}
    timestamp = float(item.get("createTime") or 0)
    upload_date = (
        datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d")
        if timestamp > 0
        else "Not available"
    )
    author = (
        author_data.get("uniqueId")
        or author_data.get("nickName")
        or author_data.get("nickname")
        or "Not available"
    )
    return path, {
        "title": str(item.get("text") or f"TikTok {video_id}")[:140],
        "filename": path.name,
        "author": str(author)[:100],
        "platform": "TikTok",
        "upload_date": upload_date,
        "timestamp": timestamp,
        "description": str(item.get("text") or "TikTok video supplied by URL.")[:500],
        "source_url": normalized_url[:2048],
        "source_type": "tiktok_url",
        "view_count": _safe_count(item.get("playCount")),
        "like_count": _safe_count(item.get("diggCount")),
    }


def _parse_embed_video_data(document: str, video_id: str) -> dict[str, Any]:
    script = re.search(
        r'<script[^>]+id=["\']__FRONTITY_CONNECT_STATE__["\'][^>]*>(.*?)</script>',
        document,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if script:
        try:
            payload = json.loads(script.group(1))
            data = payload["source"]["data"][f"/embed/v2/{video_id}"]["videoData"]
            if isinstance(data, dict):
                return data
        except (KeyError, TypeError, json.JSONDecodeError):
            pass

    for script_id in ("__UNIVERSAL_DATA_FOR_REHYDRATION__", "SIGI_STATE"):
        candidate = re.search(
            rf'<script[^>]+id=["\']{script_id}["\'][^>]*>(.*?)</script>',
            document,
            flags=re.DOTALL | re.IGNORECASE,
        )
        if not candidate:
            continue
        try:
            payload = json.loads(candidate.group(1))
            item = _find_tiktok_item(payload, video_id)
            if item:
                return _normalise_item_data(item)
        except (TypeError, json.JSONDecodeError):
            continue
    raise RuntimeError("TikTok's public embed did not include readable video information.")


def _is_tiktok_cdn_url(value: object) -> bool:
    parsed = urlsplit(str(value or ""))
    hostname = (parsed.hostname or "").rstrip(".").lower()
    cdn_suffixes = (
        "tiktokcdn.com",
        "tiktokcdn-us.com",
        "ibytedtos.com",
        "byteoversea.com",
    )
    return parsed.scheme == "https" and not parsed.username and not parsed.password and any(
        hostname == suffix or hostname.endswith(f".{suffix}") for suffix in cdn_suffixes
    )


def _is_tiktok_page_url(value: object) -> bool:
    parsed = urlsplit(str(value or ""))
    hostname = (parsed.hostname or "").rstrip(".").lower()
    return parsed.scheme == "https" and not parsed.username and not parsed.password and (
        hostname == "tiktok.com" or hostname.endswith(".tiktok.com")
    )


def _request_with_safe_redirects(
    requests_module: Any,
    url: str,
    *,
    validator: Any,
    timeout: int,
    max_redirects: int = 4,
    transient_retries: int = 3,
    **kwargs: Any,
) -> Any:
    """Fetch while validating DNS and every redirect target against an allowlist."""
    current = str(url)
    for redirect_count in range(max_redirects + 1):
        if not validator(current):
            raise RuntimeError("TikTok returned a media location outside the approved host allowlist.")
        _assert_public_hostname(current)
        response = None
        for attempt in range(max(0, int(transient_retries)) + 1):
            response = requests_module.get(
                current,
                timeout=timeout,
                allow_redirects=False,
                **kwargs,
            )
            if response.status_code not in {429, 500, 502, 503, 504} or attempt >= transient_retries:
                break
            time.sleep(1.0 * (2**attempt))
        if response is None:  # pragma: no cover - defensive request-library guard
            raise RuntimeError("TikTok could not be reached. Check the connection and retry.")
        if response.status_code not in {301, 302, 303, 307, 308}:
            return response
        if redirect_count >= max_redirects:
            raise RuntimeError("TikTok returned too many redirects.")
        location = response.headers.get("location")
        if not location:
            raise RuntimeError("TikTok returned an invalid redirect.")
        current = urljoin(current, location)
    raise RuntimeError("TikTok could not be reached safely.")


def _require_successful_tiktok_response(response: Any) -> None:
    """Convert upstream HTTP failures into stable, user-safe guidance."""
    status = int(getattr(response, "status_code", 0) or 0)
    if 200 <= status < 400:
        return
    if status in {429, 500, 502, 503, 504}:
        raise RuntimeError(
            "TikTok could not provide this video through public web access right now. "
            "Retry later, or save both videos yourself and use the upload option."
        )
    if status in {401, 403}:
        raise RuntimeError(
            "TikTok could not provide this video without additional access. "
            "Confirm that the post opens without signing in, or use the upload option."
        )
    if status == 404:
        raise RuntimeError(
            "TikTok could not provide this video. It may be private, deleted, restricted, or unavailable."
        )
    raise RuntimeError(
        "TikTok could not provide this video through public web access. Retry later or use the upload option."
    )


def _assert_public_hostname(url: str) -> None:
    hostname = urlsplit(url).hostname or ""
    try:
        addresses = {
            entry[4][0]
            for entry in socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
        }
    except socket.gaierror as exc:
        raise RuntimeError("TikTok could not be reached. Check the connection and retry.") from exc
    if not addresses:
        raise RuntimeError("TikTok could not be reached. Check the connection and retry.")
    for address in addresses:
        parsed = ipaddress.ip_address(address)
        if not parsed.is_global:
            raise RuntimeError("TikTok resolved to a non-public network address and was blocked.")


def _find_tiktok_item(payload: Any, video_id: str) -> dict[str, Any] | None:
    if isinstance(payload, dict):
        if str(payload.get("id") or payload.get("itemId") or "") == video_id and isinstance(payload.get("video"), dict):
            return payload
        item_module = payload.get("ItemModule")
        if isinstance(item_module, dict) and isinstance(item_module.get(video_id), dict):
            return item_module[video_id]
        for value in payload.values():
            found = _find_tiktok_item(value, video_id)
            if found:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = _find_tiktok_item(value, video_id)
            if found:
                return found
    return None


def _normalise_item_data(item: dict[str, Any]) -> dict[str, Any]:
    video = item.get("video") if isinstance(item.get("video"), dict) else {}
    stats = item.get("stats") if isinstance(item.get("stats"), dict) else {}
    author = item.get("author") if isinstance(item.get("author"), dict) else {}
    urls: list[str] = []
    for key in ("playAddr", "downloadAddr"):
        value = video.get(key)
        if isinstance(value, str):
            urls.append(value)
        elif isinstance(value, dict):
            url_list = value.get("urlList") or value.get("UrlList") or []
            urls.extend(str(url) for url in url_list if isinstance(url, str))
    return {
        "itemInfos": {
            "id": item.get("id"),
            "text": item.get("desc") or item.get("text"),
            "createTime": item.get("createTime"),
            "playCount": stats.get("playCount"),
            "diggCount": stats.get("diggCount"),
            "video": {
                "urls": urls,
                "videoMeta": {"duration": video.get("duration") or 0},
            },
        },
        "authorInfos": author,
    }


def _safe_download_error(exc: Exception) -> str:
    message = str(exc).replace("ERROR:", "").strip()
    if "larger than max-filesize" in message.lower():
        return "The TikTok video is larger than the configured limit."
    if any(term in message.lower() for term in ("unable to download", "failed to establish", "timed out")):
        return "TikTok could not be reached. Check the connection and confirm that both posts are public."
    return "TikTok could not provide this video. It may be private, deleted, restricted, or unavailable."


def _display_date(value: object) -> str:
    raw = str(value or "")
    try:
        return datetime.strptime(raw, "%Y%m%d").strftime("%Y-%m-%d")
    except ValueError:
        return "Not available"


def _safe_count(value: object) -> int | None:
    try:
        return max(0, int(value)) if value is not None else None
    except (TypeError, ValueError):
        return None
