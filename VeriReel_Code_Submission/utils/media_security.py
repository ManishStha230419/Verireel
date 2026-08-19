"""Security checks and integrity metadata for untrusted video inputs.

The checks deliberately run before the fingerprinting pipeline reaches the
more expensive frame-analysis stage. They are validation controls, not a
guarantee that a media decoder is free from vulnerabilities.
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2


VIDEO_SIGNATURES = {
    "iso_base_media": {".mp4", ".mov", ".m4v"},
    "avi": {".avi"},
    "matroska": {".webm", ".mkv"},
}


class MediaValidationError(ValueError):
    """A user-safe validation failure for an uploaded/downloaded file."""


def inspect_video(
    path: Path,
    *,
    max_bytes: int,
    max_duration_seconds: int,
    max_video_pixels: int,
) -> dict[str, Any]:
    """Validate a video container, probe it, and return integrity metadata."""
    media_path = Path(path).resolve()
    if not media_path.is_file():
        raise MediaValidationError("The submitted video could not be read safely.")

    size = media_path.stat().st_size
    if size <= 0:
        raise MediaValidationError("One of the submitted videos is empty.")
    if size > max(1, int(max_bytes)):
        raise MediaValidationError("One of the submitted videos exceeds the configured size limit.")

    container = _detect_container(media_path)
    extension = media_path.suffix.lower()
    if extension not in VIDEO_SIGNATURES.get(container, set()):
        raise MediaValidationError(
            "A video file's contents do not match its filename extension. "
            "Export it as MP4, MOV, AVI, WebM, or MKV and try again."
        )

    probe = _probe_video(media_path)
    width = probe["width"]
    height = probe["height"]
    if width * height > max(1, int(max_video_pixels)):
        raise MediaValidationError("A submitted video exceeds the configured resolution limit.")
    duration = probe["duration"]
    if duration <= 0:
        raise MediaValidationError("A submitted video has no readable duration.")
    if duration > max(1, int(max_duration_seconds)):
        raise MediaValidationError(
            f"Videos must be {max_duration_seconds} seconds or shorter for this comparison."
        )

    try:
        os.chmod(media_path, 0o600)
    except OSError:
        pass

    return {
        "sha256": _sha256(media_path),
        "integrity_algorithm": "SHA-256",
        "size_bytes": size,
        "detected_container": container,
        "acquired_at": datetime.now(timezone.utc).isoformat(),
        "security_probe": {
            "readable": True,
            "duration_seconds": round(duration, 3),
            "width": width,
            "height": height,
            "fps": round(probe["fps"], 3),
        },
    }


def _detect_container(path: Path) -> str:
    with path.open("rb") as handle:
        header = handle.read(16)
    if len(header) >= 12 and header[4:8] == b"ftyp":
        return "iso_base_media"
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"AVI ":
        return "avi"
    if header.startswith(b"\x1aE\xdf\xa3"):
        return "matroska"
    raise MediaValidationError(
        "The submitted file is not a recognised MP4, MOV, AVI, WebM, or MKV video container."
    )


def _probe_video(path: Path) -> dict[str, Any]:
    capture = cv2.VideoCapture(str(path))
    try:
        if not capture.isOpened():
            raise MediaValidationError("The submitted video cannot be decoded.")
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0)
        frame_count = float(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        readable, frame = capture.read()
        if not readable or frame is None or width <= 0 or height <= 0:
            raise MediaValidationError("The submitted video does not contain a readable frame.")
        if not 0.1 <= fps <= 240:
            raise MediaValidationError("The submitted video reports an unsafe or invalid frame rate.")
        duration = frame_count / fps if frame_count > 0 else 0.0
        return {"width": width, "height": height, "fps": fps, "duration": duration}
    finally:
        capture.release()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
