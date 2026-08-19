"""Run the frozen VeriReel controlled pilot and save auditable results."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import platform
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import cv2
import imagehash
import numpy as np
from PIL import __version__ as pillow_version

from evaluation.baselines import single_phash_baseline
from evaluation.metrics import (
    average_precision,
    bootstrap_intervals,
    classification_metrics,
    recall_by_group,
    rounded,
    threshold_curve,
)
from utils.fingerprint import FUSION_WEIGHTS, compare_videos, extract_fingerprint


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_manifest(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    required = {"pair_id", "video_1", "video_2", "label", "pair_kind", "transformation", "split"}
    if not rows or not required.issubset(rows[0]):
        raise ValueError(f"Manifest must contain: {', '.join(sorted(required))}")
    return rows


def _score_summary(records: list[dict[str, Any]], score_key: str) -> dict[str, dict[str, float | int]]:
    result = {}
    for label, name in ((1, "positive"), (0, "negative")):
        values = [float(record[score_key]) for record in records if int(record["label"]) == label]
        result[name] = {
            "count": len(values),
            "minimum": min(values) if values else 0.0,
            "mean": sum(values) / len(values) if values else 0.0,
            "maximum": max(values) if values else 0.0,
        }
    return result


def _verdict(score: float, threshold: float, review_band: float) -> str:
    if score >= threshold:
        return "MATCH_CANDIDATE"
    if score >= max(40.0, threshold - review_band):
        return "REVIEW_REQUIRED"
    return "NO_STRONG_MATCH"


def run(manifest_path: Path, config_path: Path, results_dir: Path) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    rows = _load_manifest(manifest_path)
    if config["expected_fusion_weights"] != FUSION_WEIGHTS:
        raise RuntimeError(
            "The scoring weights differ from frozen_config.json. Create a new protocol version before evaluating."
        )
    requested_split = config["split"]
    rows = [row for row in rows if row["split"] == requested_split]
    if not rows:
        raise ValueError(f"No rows found for split {requested_split!r}")

    threshold = float(config["decision_threshold"])
    review_band = float(config["review_band_points"])
    hamming_threshold = float(config["baseline"]["mean_hamming_threshold"])
    results_dir.mkdir(parents=True, exist_ok=True)

    fingerprint_cache: dict[Path, dict[str, Any]] = {}
    extraction_seconds: dict[Path, float] = {}

    def fingerprint(relative_path: str) -> dict[str, Any]:
        path = (manifest_path.parent / relative_path).resolve()
        if path not in fingerprint_cache:
            started = time.perf_counter()
            fingerprint_cache[path] = extract_fingerprint(
                path,
                sample_fps=float(config["sample_fps"]),
                max_samples=int(config["max_samples"]),
            )
            extraction_seconds[path] = time.perf_counter() - started
        return fingerprint_cache[path]

    predictions: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    benchmark_started = time.perf_counter()
    for index, row in enumerate(rows, 1):
        try:
            fp1 = fingerprint(row["video_1"])
            fp2 = fingerprint(row["video_2"])
            comparison_started = time.perf_counter()
            similarity = compare_videos(fp1, fp2)
            comparison_seconds = time.perf_counter() - comparison_started
            baseline = single_phash_baseline(
                fp1,
                fp2,
                hamming_threshold=hamming_threshold,
            )
            score = float(similarity["overall"])
            predictions.append(
                {
                    **row,
                    "label": int(row["label"]),
                    "overall_score": score,
                    "prediction": int(score >= threshold),
                    "verdict": _verdict(score, threshold, review_band),
                    "perceptual": similarity["perceptual"],
                    "phash": similarity["phash"],
                    "whash": similarity["whash"],
                    "dhash": similarity["dhash"],
                    "ahash": similarity["ahash"],
                    "temporal": similarity["temporal"],
                    "color": similarity["color"],
                    "motion": similarity["motion"],
                    "support_gate": similarity["support_gate"],
                    "orientation": similarity["alignment"]["orientation"],
                    "time_scale": similarity["alignment"]["time_scale"],
                    "matched_frames": similarity["alignment"]["matched_frames"],
                    "longer_video_coverage": similarity["alignment"]["longer_video_coverage"],
                    "comparison_seconds": round(comparison_seconds, 6),
                    "baseline_score": baseline["score"],
                    "baseline_mean_hamming": baseline["mean_hamming"],
                    "baseline_prediction": baseline["prediction"],
                }
            )
        except Exception as exc:  # Retain failures instead of silently dropping them.
            failures.append({"pair_id": row["pair_id"], "error": f"{type(exc).__name__}: {exc}"})
        if index % 24 == 0 or index == len(rows):
            print(f"Processed {index}/{len(rows)} pairs")
    total_seconds = time.perf_counter() - benchmark_started

    if not predictions:
        raise RuntimeError("No pair completed successfully; metrics cannot be calculated.")

    prediction_fields = list(predictions[0])
    predictions_path = results_dir / "predictions.csv"
    with predictions_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=prediction_fields)
        writer.writeheader()
        writer.writerows(predictions)

    thresholds = list(range(50, 96))
    curve = threshold_curve(predictions, thresholds, score_key="overall_score")
    curve_path = results_dir / "threshold_curve.csv"
    with curve_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(curve[0]))
        writer.writeheader()
        writer.writerows(curve)

    model_metrics = classification_metrics(
        predictions,
        threshold=threshold,
        score_key="overall_score",
    )
    baseline_metrics = classification_metrics(
        predictions,
        prediction_key="baseline_prediction",
    )
    model_metrics["average_precision"] = average_precision(predictions, "overall_score")
    baseline_metrics["average_precision"] = average_precision(predictions, "baseline_score")

    extraction_total = sum(extraction_seconds.values())
    comparison_total = sum(float(row["comparison_seconds"]) for row in predictions)
    mean_extraction = extraction_total / len(extraction_seconds)
    mean_comparison = comparison_total / len(predictions)
    review_counts = {
        verdict: sum(row["verdict"] == verdict for row in predictions)
        for verdict in ("MATCH_CANDIDATE", "REVIEW_REQUIRED", "NO_STRONG_MATCH")
    }

    summary = {
        "evaluation": {
            "name": config["evaluation_name"],
            "protocol_version": config["protocol_version"],
            "claim_boundary": config["claim_boundary"],
            "split": requested_split,
            "manifest_sha256": _sha256(manifest_path),
            "config_sha256": _sha256(config_path),
            "fingerprint_code_sha256": _sha256(PROJECT_ROOT / "utils" / "fingerprint.py"),
            "runner_code_sha256": _sha256(Path(__file__)),
        },
        "dataset": {
            "manifest_pairs": len(rows),
            "completed_pairs": len(predictions),
            "failed_pairs": len(failures),
            "positive_pairs": sum(int(row["label"]) for row in predictions),
            "negative_pairs": sum(1 - int(row["label"]) for row in predictions),
            "unique_videos": len(fingerprint_cache),
            "transformations": sorted({row["transformation"] for row in predictions}),
        },
        "decision": {
            "threshold": threshold,
            "review_band_points": review_band,
            "outcome_counts": review_counts,
        },
        "verireel": {
            "metrics": model_metrics,
            "bootstrap_95_intervals": bootstrap_intervals(
                predictions,
                threshold=threshold,
                score_key="overall_score",
                iterations=int(config["bootstrap_iterations"]),
                seed=int(config["bootstrap_seed"]),
            ),
            "score_summary": _score_summary(predictions, "overall_score"),
            "per_transformation": recall_by_group(
                predictions,
                group_key="transformation",
                threshold=threshold,
                score_key="overall_score",
            ),
        },
        "baseline": {
            "name": config["baseline"]["name"],
            "mean_hamming_threshold": hamming_threshold,
            "metrics": baseline_metrics,
            "score_summary": _score_summary(predictions, "baseline_score"),
            "per_transformation": recall_by_group(
                predictions,
                group_key="transformation",
                prediction_key="baseline_prediction",
            ),
        },
        "timing": {
            "total_wall_seconds": total_seconds,
            "fingerprint_extraction_seconds": extraction_total,
            "mean_fingerprint_seconds_per_unique_video": mean_extraction,
            "comparison_seconds": comparison_total,
            "mean_comparison_seconds_per_pair": mean_comparison,
            "estimated_uncached_pair_seconds": 2 * mean_extraction + mean_comparison,
            "estimated_uncached_pairs_per_minute": 60.0 / (2 * mean_extraction + mean_comparison),
        },
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "opencv": cv2.__version__,
            "numpy": np.__version__,
            "pillow": pillow_version,
            "imagehash": getattr(imagehash, "__version__", "not exposed"),
        },
        "threshold_curve": curve,
        "failures": failures,
    }
    summary = rounded(summary)
    metrics_path = results_dir / "metrics.json"
    metrics_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (results_dir / "failures.json").write_text(json.dumps(failures, indent=2), encoding="utf-8")
    print(f"Saved predictions: {predictions_path}")
    print(f"Saved metrics: {metrics_path}")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path(__file__).parent / "data" / "manifest.csv",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).parent / "frozen_config.json",
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=Path(__file__).parent / "results",
    )
    args = parser.parse_args()
    run(args.manifest.resolve(), args.config.resolve(), args.results_dir.resolve())


if __name__ == "__main__":
    main()

