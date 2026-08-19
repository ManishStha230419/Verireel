"""Privacy-conscious JSON security-event logging."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any


def build_security_logger(project_root: Path) -> logging.Logger:
    logger = logging.getLogger("verireel.security")
    if logger.handlers:
        return logger
    log_dir = (Path(project_root) / "logs").resolve()
    log_path = log_dir / "security-events.jsonl"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path.touch(exist_ok=True)
        if os.name != "nt":
            os.chmod(log_dir, 0o700)
            os.chmod(log_path, 0o600)
        handler: logging.Handler = RotatingFileHandler(
            log_path,
            maxBytes=2 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
    except OSError:
        # Logging must never prevent the application from starting. If a stale
        # directory has an incompatible ACL, retain security events on stderr.
        handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


def audit_event(logger: logging.Logger, event: str, **fields: Any) -> None:
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": str(event)[:80],
    }
    for key, value in fields.items():
        if value is None:
            continue
        record[str(key)[:60]] = _safe_value(value)
    logger.info(json.dumps(record, ensure_ascii=True, separators=(",", ":")))


def _safe_value(value: Any) -> Any:
    if isinstance(value, bool) or isinstance(value, (int, float)):
        return value
    return str(value).replace("\r", " ").replace("\n", " ")[:240]
