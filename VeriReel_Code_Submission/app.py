"""VeriReel Flask application.

The web demo accepts either two local videos or two user-supplied TikTok post
links. In both modes, source media is kept in an isolated temporary directory
and deleted as soon as analysis finishes.
"""

from __future__ import annotations

import logging
import os
import hashlib
import hmac
import secrets
import shutil
import threading
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv
from flask import Flask, g, has_request_context, jsonify, request, send_file
from werkzeug.datastructures import FileStorage
from werkzeug.exceptions import RequestEntityTooLarge, SecurityError
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename

from utils.fingerprint import compare_videos, extract_fingerprint
from utils.downloader import download_tiktok, validate_tiktok_url
from utils.audit import audit_event, build_security_logger
from utils.media_security import MediaValidationError, inspect_video
from utils.pdf_report import build_pdf_report
from utils.reporter import generate_report


load_dotenv()


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name, "1" if default else "0")
    return value.strip().lower() in {"1", "true", "yes", "on"}

PROJECT_ROOT = Path(__file__).resolve().parent
TEMP_DIR = (PROJECT_ROOT / "temp").resolve()
TEMP_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".webm", ".mkv"}
MAX_UPLOAD_MB = max(10, int(os.environ.get("MAX_UPLOAD_MB", "200")))
DEFAULT_THRESHOLD = float(os.environ.get("SIMILARITY_THRESHOLD", "75"))
JOB_TTL_SECONDS = max(300, int(os.environ.get("JOB_TTL_SECONDS", "3600")))
WORKER_COUNT = max(1, min(4, int(os.environ.get("WORKER_COUNT", "2"))))
MAX_REMOTE_DURATION_SECONDS = max(15, int(os.environ.get("MAX_REMOTE_DURATION_SECONDS", "300")))
YTDLP_SOCKET_TIMEOUT = max(5, int(os.environ.get("YTDLP_SOCKET_TIMEOUT", "20")))
MAX_UPLOAD_DURATION_SECONDS = max(15, int(os.environ.get("MAX_UPLOAD_DURATION_SECONDS", "300")))
MAX_VIDEO_PIXELS = max(640 * 480, int(os.environ.get("MAX_VIDEO_PIXELS", str(3840 * 2160))))
RATE_LIMIT_REQUESTS = max(2, int(os.environ.get("RATE_LIMIT_REQUESTS", "8")))
RATE_LIMIT_WINDOW_SECONDS = max(10, int(os.environ.get("RATE_LIMIT_WINDOW_SECONDS", "60")))
MAX_PENDING_JOBS = max(WORKER_COUNT, int(os.environ.get("MAX_PENDING_JOBS", "6")))
MAX_JOBS_PER_CLIENT = max(1, int(os.environ.get("MAX_JOBS_PER_CLIENT", "2")))
MIN_FREE_DISK_MB = max(50, int(os.environ.get("MIN_FREE_DISK_MB", "512")))
TRUSTED_PROXY_HOPS = max(0, min(5, int(os.environ.get("TRUSTED_PROXY_HOPS", "0"))))
TRUSTED_HOSTS = [
    host.strip()
    for host in os.environ.get("TRUSTED_HOSTS", "127.0.0.1,localhost,[::1]").split(",")
    if host.strip()
]
ENABLE_HSTS = _env_flag("ENABLE_HSTS")
EXPOSE_HEALTH_DETAILS = _env_flag("EXPOSE_HEALTH_DETAILS")

if not TRUSTED_HOSTS:
    raise RuntimeError("TRUSTED_HOSTS must contain at least one allowed host.")

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.config.update(
    MAX_CONTENT_LENGTH=(MAX_UPLOAD_MB * 2 + 4) * 1024 * 1024,
    MAX_FORM_MEMORY_SIZE=64 * 1024,
    MAX_FORM_PARTS=16,
    JSON_SORT_KEYS=False,
    TRUSTED_HOSTS=TRUSTED_HOSTS,
    ENABLE_HSTS=ENABLE_HSTS,
    EXPOSE_HEALTH_DETAILS=EXPOSE_HEALTH_DETAILS,
)
if TRUSTED_PROXY_HOPS:
    # Only enable this when exactly this many trusted reverse proxies sit in
    # front of the app. Direct public access must be blocked at the firewall.
    app.wsgi_app = ProxyFix(
        app.wsgi_app,
        x_for=TRUSTED_PROXY_HOPS,
        x_proto=TRUSTED_PROXY_HOPS,
    )

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("verireel")
security_logger = build_security_logger(PROJECT_ROOT)

_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.RLock()
_rate_windows: dict[str, deque[float]] = {}
_server_secret = secrets.token_bytes(32)
_executor = ThreadPoolExecutor(max_workers=WORKER_COUNT, thread_name_prefix="verireel")


def _audit(event: str, **fields: Any) -> None:
    """Write a privacy-conscious event with a request correlation ID."""
    if has_request_context():
        fields.setdefault("request_id", getattr(g, "request_id", None))
    audit_event(security_logger, event, **fields)


@app.before_request
def assign_request_id() -> None:
    # Generate this server-side so an attacker cannot inject log identifiers.
    g.request_id = secrets.token_hex(12)


@app.get("/")
def index():
    response = app.send_static_file("index.html")
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/api/health")
def health():
    payload: dict[str, Any] = {"status": "ok"}
    if app.config["EXPOSE_HEALTH_DETAILS"]:
        with _jobs_lock:
            active = sum(
                1
                for job in _jobs.values()
                if job.get("status") in {"queued", "processing", "cancelling"}
            )
        payload.update(
            {"workers": WORKER_COUNT, "active_jobs": active, "queue_limit": MAX_PENDING_JOBS}
        )
    return jsonify(payload)


@app.post("/api/analyze")
def analyze():
    """Queue a comparison of two uploads or two supplied TikTok post links."""
    _purge_expired_jobs()
    guard = _state_change_guard()
    if guard is not None:
        return guard
    client_key = _client_key()
    admission = _admission_guard(client_key)
    if admission is not None:
        return admission
    if request.is_json:
        if request.content_length and request.content_length > 16 * 1024:
            return jsonify({"error": "The TikTok link request is too large."}), 413
        return _queue_tiktok_analysis(request.get_json(silent=True), client_key)
    if not request.mimetype or not request.mimetype.startswith("multipart/form-data"):
        return jsonify({"error": "Submit two local video files or two TikTok video links."}), 415

    video1 = request.files.get("video1")
    video2 = request.files.get("video2")
    if not video1 or not video2 or not video1.filename or not video2.filename:
        return jsonify({"error": "Both video files are required."}), 400

    try:
        threshold = _parse_threshold(request.form.get("threshold"))
        meta1 = _request_metadata(video1, 1)
        meta2 = _request_metadata(video2, 2)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    job_id = str(uuid.uuid4())
    access_token = secrets.token_urlsafe(32)
    job_dir = _create_job_directory(job_id)

    try:
        path1 = _save_upload(video1, job_dir, "video1")
        path2 = _save_upload(video2, job_dir, "video2")
        meta1.update(_inspect_input(path1, MAX_UPLOAD_DURATION_SECONDS))
        meta2.update(_inspect_input(path2, MAX_UPLOAD_DURATION_SECONDS))
    except (OSError, ValueError, MediaValidationError) as exc:
        _remove_job_directory(job_dir)
        _audit("analysis_rejected", client=client_key, source_mode="upload", reason="media_validation")
        return jsonify({"error": str(exc)}), 400

    now = time.time()
    with _jobs_lock:
        _jobs[job_id] = {
            "status": "queued",
            "progress": 5,
            "message": "Uploads received. Waiting for an analysis worker.",
            "created_at": now,
            "updated_at": now,
            "cancel_requested": False,
            "source_mode": "upload",
            "access_token_digest": _token_digest(access_token),
            "client_key": client_key,
        }
    _audit("analysis_queued", job=job_id[:8], client=client_key, source_mode="upload")

    _executor.submit(
        _process,
        job_id,
        path1,
        path2,
        job_dir,
        meta1,
        meta2,
        threshold,
    )
    return jsonify({"job_id": job_id, "access_token": access_token, "status_url": f"/api/status/{job_id}"}), 202


def _queue_tiktok_analysis(payload: Any, client_key: str):
    if not isinstance(payload, dict):
        return jsonify({"error": "Provide two TikTok video links in a JSON object."}), 400
    try:
        threshold = _parse_threshold(payload.get("threshold"))
        url1 = validate_tiktok_url(payload.get("url1"))
        url2 = validate_tiktok_url(payload.get("url2"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    job_id = str(uuid.uuid4())
    access_token = secrets.token_urlsafe(32)
    job_dir = _create_job_directory(job_id)
    now = time.time()
    with _jobs_lock:
        _jobs[job_id] = {
            "status": "queued",
            "progress": 3,
            "message": "TikTok links accepted. Waiting for a download worker.",
            "created_at": now,
            "updated_at": now,
            "cancel_requested": False,
            "source_mode": "tiktok",
            "access_token_digest": _token_digest(access_token),
            "client_key": client_key,
        }

    _audit("analysis_queued", job=job_id[:8], client=client_key, source_mode="tiktok")
    _executor.submit(_process_tiktok, job_id, url1, url2, job_dir, threshold)
    return jsonify({"job_id": job_id, "access_token": access_token, "status_url": f"/api/status/{job_id}"}), 202


@app.get("/api/status/<job_id>")
def status(job_id: str):
    _purge_expired_jobs()
    if not _valid_job_id(job_id):
        return jsonify({"error": "Invalid job identifier."}), 400
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None or not _token_matches(job, request.headers.get("X-Job-Token")):
            return jsonify({"error": "Job not found or expired."}), 404
        response = {
            key: job[key]
            for key in ("status", "progress", "message", "source_mode", "created_at", "updated_at", "result")
            if key in job
        }
    return jsonify(response)


@app.delete("/api/status/<job_id>")
def cancel(job_id: str):
    guard = _state_change_guard()
    if guard is not None:
        return guard
    if not _valid_job_id(job_id):
        return jsonify({"error": "Invalid job identifier."}), 400
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None or not _token_matches(job, request.headers.get("X-Job-Token")):
            return jsonify({"error": "Job not found or expired."}), 404
        if job["status"] in {"complete", "error", "cancelled"}:
            return jsonify({"status": job["status"]})
        job.update(
            {
                "cancel_requested": True,
                "status": "cancelling",
                "message": "Stopping after the current processing step.",
                "updated_at": time.time(),
            }
        )
    _audit("analysis_cancel_requested", job=job_id[:8])
    return jsonify({"status": "cancelling"}), 202


@app.get("/api/report/<job_id>.pdf")
def report_pdf(job_id: str):
    _purge_expired_jobs()
    if not _valid_job_id(job_id):
        return jsonify({"error": "Invalid job identifier."}), 400
    with _jobs_lock:
        job = _jobs.get(job_id)
        if (
            not job
            or not _token_matches(job, request.headers.get("X-Job-Token"))
            or job.get("status") != "complete"
            or "result" not in job
        ):
            return jsonify({"error": "The completed report is not available."}), 404
        result = job["result"]
    buffer = build_pdf_report(result)
    return send_file(
        buffer,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"verireel-report-{job_id[:8]}.pdf",
        max_age=0,
    )


@app.errorhandler(RequestEntityTooLarge)
def too_large(_: RequestEntityTooLarge):
    return (
        jsonify(
            {
                "error": (
                    f"The combined upload is too large. Use two files no larger than "
                    f"{MAX_UPLOAD_MB} MB each."
                )
            }
        ),
        413,
    )


@app.errorhandler(SecurityError)
def rejected_security_request(_: SecurityError):
    _audit("request_rejected", client=_client_key(), reason="untrusted_host")
    return jsonify({"error": "The request host is not allowed."}), 400


@app.after_request
def secure_response(response):
    response.headers.setdefault("X-Request-ID", getattr(g, "request_id", secrets.token_hex(12)))
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("X-DNS-Prefetch-Control", "off")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
    response.headers.setdefault("X-Permitted-Cross-Domain-Policies", "none")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self'; style-src 'self'; style-src-attr 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; "
        "form-action 'self'; frame-ancestors 'none'",
    )
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    elif request.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    if app.config["ENABLE_HSTS"] and request.is_secure:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    return response


def _process(
    job_id: str,
    path1: Path,
    path2: Path,
    job_dir: Path,
    meta1: dict[str, Any],
    meta2: dict[str, Any],
    threshold: float,
) -> None:
    def work() -> None:
        _run_analysis(
            job_id,
            path1,
            path2,
            meta1,
            meta2,
            threshold,
            progress_points=(15, 45, 72, 90),
        )

    _execute_job(job_id, job_dir, work)


def _process_tiktok(
    job_id: str,
    url1: str,
    url2: str,
    job_dir: Path,
    threshold: float,
) -> None:
    def work() -> None:
        _check_cancelled(job_id)
        _update_job(job_id, 8, "Downloading TikTok Video 1 into temporary storage.")
        path1, meta1 = download_tiktok(
            url1,
            job_dir,
            "video1",
            max_video_mb=MAX_UPLOAD_MB,
            max_duration_seconds=MAX_REMOTE_DURATION_SECONDS,
            socket_timeout=YTDLP_SOCKET_TIMEOUT,
        )
        meta1.update(_inspect_input(path1, MAX_REMOTE_DURATION_SECONDS))

        _check_cancelled(job_id)
        _update_job(job_id, 24, "Downloading TikTok Video 2 into temporary storage.")
        path2, meta2 = download_tiktok(
            url2,
            job_dir,
            "video2",
            max_video_mb=MAX_UPLOAD_MB,
            max_duration_seconds=MAX_REMOTE_DURATION_SECONDS,
            socket_timeout=YTDLP_SOCKET_TIMEOUT,
        )
        meta2.update(_inspect_input(path2, MAX_REMOTE_DURATION_SECONDS))

        _run_analysis(
            job_id,
            path1,
            path2,
            meta1,
            meta2,
            threshold,
            progress_points=(40, 61, 78, 92),
        )

    _execute_job(job_id, job_dir, work)


def _run_analysis(
    job_id: str,
    path1: Path,
    path2: Path,
    meta1: dict[str, Any],
    meta2: dict[str, Any],
    threshold: float,
    *,
    progress_points: tuple[int, int, int, int],
) -> None:
    first_progress, second_progress, compare_progress, report_progress = progress_points
    _check_cancelled(job_id)
    _update_job(job_id, first_progress, "Extracting four hashes and signal features from Video 1.")
    fingerprint1 = extract_fingerprint(path1)
    _enrich_metadata(meta1, fingerprint1)

    _check_cancelled(job_id)
    _update_job(job_id, second_progress, "Extracting four hashes and signal features from Video 2.")
    fingerprint2 = extract_fingerprint(path2)
    _enrich_metadata(meta2, fingerprint2)

    _check_cancelled(job_id)
    _update_job(job_id, compare_progress, "Running sliding-window and transformation-aware comparison.")
    similarity = compare_videos(fingerprint1, fingerprint2)

    _check_cancelled(job_id)
    _update_job(job_id, report_progress, "Building the comparison report.")
    report = generate_report(similarity, meta1, meta2, threshold)
    result = {
        "similarity": similarity,
        "video1": meta1,
        "video2": meta2,
        "report": report,
        "security": {
            "job_access": "Protected by a high-entropy bearer credential stored as a SHA-256 digest.",
            "input_validation": "Container signature, size, duration, resolution, frame-rate, and readable-frame checks passed.",
            "integrity": "SHA-256 evidence digests calculated before fingerprint extraction.",
            "retention": "Source media scheduled for immediate deletion after report construction.",
        },
    }

    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return
        job.update(
            {
                "status": "processing",
                "progress": 98,
                "message": "Finalising the report and deleting source media.",
                "updated_at": time.time(),
                "result": result,
            }
        )


def _execute_job(job_id: str, job_dir: Path, operation: Callable[[], None]) -> None:
    try:
        operation()
    except _Cancelled:
        with _jobs_lock:
            if job_id in _jobs:
                _jobs[job_id].update(
                    {
                        "status": "cancelled",
                        "progress": 0,
                        "message": "Analysis cancelled. Source videos have been deleted.",
                        "updated_at": time.time(),
                    }
                )
        _audit("analysis_cancelled", job=job_id[:8])
    except Exception as exc:  # Worker boundary: record a safe message and always clean up.
        logger.exception("Analysis job %s failed", job_id)
        with _jobs_lock:
            source_mode = str(_jobs.get(job_id, {}).get("source_mode") or "unknown")
        message = _public_worker_error(exc, source_mode)
        with _jobs_lock:
            if job_id in _jobs:
                _jobs[job_id].update(
                    {
                        "status": "error",
                        "progress": 0,
                        "message": message[:400],
                        "updated_at": time.time(),
                    }
                )
        _audit("analysis_failed", job=job_id[:8], source_mode=source_mode, error_type=type(exc).__name__)
    finally:
        _remove_job_directory(job_dir)
        with _jobs_lock:
            job = _jobs.get(job_id)
            if job and job.get("status") == "processing" and "result" in job:
                deleted_at = datetime.now(timezone.utc).isoformat()
                job["result"]["security"].update({"retention": "Source media deleted after analysis.", "media_deleted_at": deleted_at})
                job.update(
                    {
                        "status": "complete",
                        "progress": 100,
                        "message": "Analysis complete. Source videos have been deleted.",
                        "updated_at": time.time(),
                    }
                )
                _audit("analysis_completed", job=job_id[:8], source_mode=job.get("source_mode"))


def _save_upload(upload: FileStorage, job_dir: Path, slot: str) -> Path:
    original_name = secure_filename(upload.filename or "")
    extension = Path(original_name).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
        raise ValueError(f"Unsupported video format. Allowed formats: {allowed}.")
    path = (job_dir / f"{slot}{extension}").resolve()
    if path.parent != job_dir:
        raise ValueError("Invalid upload path.")
    upload.save(path)
    _restrict_file_permissions(path)
    size = path.stat().st_size
    if size <= 0:
        raise ValueError("One of the uploaded files is empty.")
    if size > MAX_UPLOAD_MB * 1024 * 1024:
        raise ValueError(f"Each video must be no larger than {MAX_UPLOAD_MB} MB.")
    return path


def _inspect_input(path: Path, max_duration_seconds: int) -> dict[str, Any]:
    _restrict_file_permissions(path)
    return inspect_video(
        path,
        max_bytes=MAX_UPLOAD_MB * 1024 * 1024,
        max_duration_seconds=max_duration_seconds,
        max_video_pixels=MAX_VIDEO_PIXELS,
    )


def _request_metadata(upload: FileStorage, number: int) -> dict[str, Any]:
    label = (request.form.get(f"label{number}") or "").strip()
    publication_date = (request.form.get(f"date{number}") or "").strip()
    timestamp = 0.0
    if publication_date:
        try:
            parsed = datetime.strptime(publication_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            timestamp = parsed.timestamp()
        except ValueError as exc:
            raise ValueError(f"Video {number} has an invalid publication date.") from exc
    return {
        "title": (label or secure_filename(upload.filename or f"Video {number}"))[:140],
        "filename": secure_filename(upload.filename or f"video-{number}"),
        "author": "Not provided",
        "platform": "Local upload",
        "upload_date": publication_date or "Not provided",
        "timestamp": timestamp,
        "description": "Locally uploaded for transient fingerprint analysis.",
        "source_type": "local_upload",
    }


def _enrich_metadata(metadata: dict[str, Any], fingerprint: dict[str, Any]) -> None:
    metadata.update(
        {
            "duration": fingerprint["duration"],
            "fps": fingerprint["fps"],
            "sampled_frames": fingerprint["sampled_frames"],
            "resolution": fingerprint["resolution"],
            "codec": fingerprint["codec"],
        }
    )


def _parse_threshold(value: str | None) -> float:
    if value in (None, ""):
        value = str(DEFAULT_THRESHOLD)
    try:
        threshold = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("The review threshold must be a number.") from exc
    if not 50 <= threshold <= 95:
        raise ValueError("The review threshold must be between 50 and 95.")
    return threshold


def _update_job(job_id: str, progress: int, message: str) -> None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None or job.get("cancel_requested"):
            raise _Cancelled()
        job.update(
            {
                "status": "processing",
                "progress": progress,
                "message": message,
                "updated_at": time.time(),
            }
        )


def _check_cancelled(job_id: str) -> None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job or job.get("cancel_requested"):
            raise _Cancelled()


def _remove_job_directory(job_dir: Path) -> None:
    try:
        resolved = job_dir.resolve()
        if resolved.parent == TEMP_DIR and resolved.name:
            shutil.rmtree(resolved, ignore_errors=True)
    except OSError:
        logger.warning("Could not remove temporary directory for %s", job_dir)


def _create_job_directory(job_id: str) -> Path:
    job_dir = (TEMP_DIR / job_id).resolve()
    if job_dir.parent != TEMP_DIR or not _valid_job_id(job_id):
        raise ValueError("Invalid temporary job path.")
    job_dir.mkdir(mode=0o700, parents=False, exist_ok=False)
    try:
        os.chmod(job_dir, 0o700)
    except OSError:
        logger.warning("Could not restrict temporary directory permissions for %s", job_id[:8])
    return job_dir


def _restrict_file_permissions(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        logger.warning("Could not restrict temporary media permissions for %s", path.name)


def _purge_expired_jobs() -> None:
    cutoff = time.time() - JOB_TTL_SECONDS
    with _jobs_lock:
        expired = [
            job_id
            for job_id, job in _jobs.items()
            if job.get("updated_at", 0) < cutoff
            and job.get("status") in {"complete", "error", "cancelled"}
        ]
        for job_id in expired:
            del _jobs[job_id]


def _state_change_guard():
    """Require an intentional same-site API request for mutating operations."""
    if request.headers.get("X-VeriReel-Request") != "1":
        _audit("request_rejected", client=_client_key(), reason="missing_request_header")
        return jsonify({"error": "This request is missing the application's security header."}), 403
    if request.headers.get("Sec-Fetch-Site", "").lower() == "cross-site":
        _audit("request_rejected", client=_client_key(), reason="cross_site")
        return jsonify({"error": "Cross-site analysis requests are not allowed."}), 403
    origin = request.headers.get("Origin")
    if origin and origin.rstrip("/") != request.host_url.rstrip("/"):
        _audit("request_rejected", client=_client_key(), reason="origin_mismatch")
        return jsonify({"error": "The request origin is not allowed."}), 403
    return None


def _admission_guard(client_key: str):
    """Bound request rate, active work, and temporary-storage pressure."""
    now = time.time()
    with _jobs_lock:
        window = _rate_windows.setdefault(client_key, deque())
        cutoff = now - RATE_LIMIT_WINDOW_SECONDS
        while window and window[0] < cutoff:
            window.popleft()
        if len(window) >= RATE_LIMIT_REQUESTS:
            _audit("analysis_rejected", client=client_key, reason="rate_limit")
            response = jsonify({"error": "Too many analysis requests. Wait a minute, then try again."})
            response.headers["Retry-After"] = str(RATE_LIMIT_WINDOW_SECONDS)
            return response, 429

        active_jobs = [
            job for job in _jobs.values() if job.get("status") in {"queued", "processing", "cancelling"}
        ]
        if len(active_jobs) >= MAX_PENDING_JOBS:
            _audit("analysis_rejected", client=client_key, reason="queue_full")
            return jsonify({"error": "The analysis queue is full. Try again when another job finishes."}), 503
        client_active = sum(1 for job in active_jobs if job.get("client_key") == client_key)
        if client_active >= MAX_JOBS_PER_CLIENT:
            _audit("analysis_rejected", client=client_key, reason="client_job_limit")
            return jsonify({"error": "Finish or cancel an active comparison before starting another one."}), 429
        window.append(now)

    free_bytes = shutil.disk_usage(TEMP_DIR).free
    if free_bytes < MIN_FREE_DISK_MB * 1024 * 1024:
        _audit("analysis_rejected", client=client_key, reason="low_disk")
        return jsonify({"error": "Temporary storage is low. Try again after space is available."}), 503
    return None


def _client_key() -> str:
    # request.remote_addr is rewritten by ProxyFix only when an explicit,
    # bounded trusted-proxy count is configured. Raw forwarding headers are
    # otherwise attacker-controlled and must never influence rate limits.
    address = request.remote_addr or "unknown"
    return hmac.new(_server_secret, address.encode("utf-8", "replace"), hashlib.sha256).hexdigest()[:16]


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _token_matches(job: dict[str, Any], supplied: str | None) -> bool:
    if not supplied or len(supplied) > 256:
        return False
    return hmac.compare_digest(str(job.get("access_token_digest") or ""), _token_digest(supplied))


def _public_worker_error(exc: Exception, source_mode: str) -> str:
    if isinstance(exc, (MediaValidationError, ValueError)):
        return (str(exc).strip() or "A submitted video failed validation.")[:300]
    if source_mode == "tiktok":
        message = str(exc).strip()
        allowed_phrases = (
            "TikTok videos must be",
            "The TikTok video is larger",
            "TikTok could not be reached",
            "TikTok could not provide this video",
            "TikTok could not provide this video without additional access",
            "TikTok's public embed does not expose",
            "The TikTok link does not contain",
        )
        if message.startswith(allowed_phrases):
            return message[:300]
        return (
            "TikTok could not provide one of these public posts. Confirm that both links open "
            "without signing in, then retry or use the upload option."
        )
    return "The video analysis could not be completed safely. Check the files and try again."


def _cleanup_stale_temp_directories() -> None:
    cutoff = time.time() - max(JOB_TTL_SECONDS, 3600)
    for candidate in TEMP_DIR.iterdir():
        try:
            if (
                candidate.is_dir()
                and _valid_job_id(candidate.name)
                and candidate.stat().st_mtime < cutoff
            ):
                resolved = candidate.resolve()
                if resolved.parent == TEMP_DIR:
                    shutil.rmtree(resolved, ignore_errors=True)
        except OSError:
            logger.warning("Could not inspect stale temporary directory %s", candidate)


def _valid_job_id(value: str) -> bool:
    try:
        return str(uuid.UUID(value)) == value.lower()
    except (ValueError, AttributeError):
        return False


class _Cancelled(Exception):
    pass


_cleanup_stale_temp_directories()


if __name__ == "__main__":
    port = int(os.environ.get("FLASK_PORT", "5000"))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="127.0.0.1", port=port, debug=debug, use_reloader=False)
