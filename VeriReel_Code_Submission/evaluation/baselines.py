"""Baseline matchers promised by the VeriReel evaluation protocol."""

from __future__ import annotations

from typing import Any

from utils.fingerprint import _best_hash_alignment


def single_phash_baseline(
    fingerprint1: dict[str, Any],
    fingerprint2: dict[str, Any],
    *,
    hamming_threshold: float = 10.0,
) -> dict[str, float | int]:
    """Evaluate a normal-orientation pHash baseline using mean aligned distance.

    The baseline intentionally omits mirroring, the supporting signals, baseline
    removal and the support gate. It represents the simplest sliding-window
    pHash matcher described in the thesis.
    """
    similarity, alignment = _best_hash_alignment(
        fingerprint1.get("hashes", []),
        fingerprint2.get("hashes", []),
        "phash",
    )
    mean_hamming = max(0.0, min(64.0, (1.0 - similarity) * 64.0))
    return {
        "score": round(similarity * 100.0, 4),
        "mean_hamming": round(mean_hamming, 4),
        "prediction": int(mean_hamming <= hamming_threshold),
        "hamming_threshold": float(hamming_threshold),
        "offset_frames": int(alignment.get("offset", 0)),
        "time_scale": round(float(alignment.get("time_scale", 1.0)), 4),
    }

