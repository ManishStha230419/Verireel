"""Transparent perceptual video fingerprinting.

The pipeline samples short-form video at one frame per second, computes four
64-bit perceptual hashes, a 96-bin RGB histogram, a temporal signature, and a
motion signature. Comparisons use sliding windows with limited time-scale
resampling so trimmed and speed-adjusted copies can still align.
"""

from __future__ import annotations

from math import ceil
from pathlib import Path
from typing import Any

import cv2
import imagehash
import numpy as np
from PIL import Image, ImageOps


HASH_KEYS = ("phash", "whash", "dhash", "ahash")
FUSION_WEIGHTS = {
    "perceptual": 0.45,
    "temporal": 0.25,
    "color": 0.20,
    "motion": 0.10,
}
SUPPORT_GATE_FLOOR = 0.25
SUPPORT_GATE_FULL = 0.65
COLOR_RANDOM_BASELINE = 0.70
MOTION_RANDOM_BASELINE = 0.50


def extract_fingerprint(
    video_path: str | Path,
    sample_fps: float = 1.0,
    max_samples: int = 240,
) -> dict[str, Any]:
    """Extract a multi-signal fingerprint from a readable video."""
    if sample_fps <= 0:
        raise ValueError("sample_fps must be greater than zero")

    path = str(video_path)
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise ValueError("The uploaded file is not a readable video.")

    total_frames = max(0, int(cap.get(cv2.CAP_PROP_FRAME_COUNT)))
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    width = max(0, int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)))
    height = max(0, int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
    duration = total_frames / fps if total_frames and fps > 0 else 0.0
    codec = _fourcc_name(int(cap.get(cv2.CAP_PROP_FOURCC)))

    hashes: list[dict[str, str]] = []
    flipped_hashes: list[dict[str, str]] = []
    histograms: list[list[float]] = []
    temporal: list[float] = []
    motion: list[list[float]] = []
    timestamps: list[float] = []

    previous_hash: imagehash.ImageHash | None = None
    previous_gray: np.ndarray | None = None

    try:
        for frame_index in _sample_indices(total_frames, fps, sample_fps, max_samples):
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
            ok, frame = cap.read()
            if not ok or frame is None:
                continue

            normalized = cv2.resize(frame, (256, 256), interpolation=cv2.INTER_AREA)
            rgb = cv2.cvtColor(normalized, cv2.COLOR_BGR2RGB)
            pil = Image.fromarray(rgb)
            normal_set = _frame_hashes(pil)
            flipped_set = _frame_hashes(ImageOps.mirror(pil))

            hashes.append({key: str(value) for key, value in normal_set.items()})
            flipped_hashes.append({key: str(value) for key, value in flipped_set.items()})
            histograms.append(_rgb_histogram(rgb))
            timestamps.append(frame_index / fps if fps > 0 else float(len(timestamps)))

            current_hash = normal_set["phash"]
            gray = cv2.cvtColor(normalized, cv2.COLOR_BGR2GRAY)
            if previous_hash is not None and previous_gray is not None:
                temporal.append(float(current_hash - previous_hash) / 64.0)
                motion.append(_motion_vector(previous_gray, gray))
            previous_hash = current_hash
            previous_gray = gray
    finally:
        cap.release()

    if not hashes:
        raise ValueError("No decodable frames were found in the uploaded video.")

    return {
        "hashes": hashes,
        "flipped_hashes": flipped_hashes,
        "histograms": histograms,
        "temporal_signature": temporal,
        "motion_signature": motion,
        "sample_timestamps": timestamps,
        "duration": round(duration, 3),
        "fps": round(fps, 3),
        "total_frames": total_frames,
        "sampled_frames": len(hashes),
        "resolution": {"width": width, "height": height},
        "codec": codec,
    }


def compare_videos(fp1: dict[str, Any], fp2: dict[str, Any]) -> dict[str, Any]:
    """Compare two fingerprints and return transparent 0-100 metrics."""
    hashes1 = fp1.get("hashes", [])
    hashes2 = fp2.get("hashes", [])
    flipped2 = fp2.get("flipped_hashes", [])
    if not hashes1 or not hashes2:
        raise ValueError("Both videos must contain at least one decodable frame.")

    normal_score, normal_alignment = _best_hash_alignment(hashes1, hashes2, "phash")
    flip_score, flip_alignment = _best_hash_alignment(hashes1, flipped2, "phash")
    if flip_score > normal_score:
        primary_alignment = {**flip_alignment, "orientation": "mirrored"}
        comparison_hashes = flipped2
    else:
        primary_alignment = {**normal_alignment, "orientation": "normal"}
        comparison_hashes = hashes2

    primary_pairs = list(primary_alignment.get("_pairs") or [])
    raw_hash_scores = {
        key: _hash_similarity_for_pairs(hashes1, comparison_hashes, key, primary_pairs)
        for key in HASH_KEYS
    }

    calibrated = {key: _calibrate_hash(value) for key, value in raw_hash_scores.items()}
    perceptual = (
        calibrated["phash"] * 0.50
        + calibrated["whash"] * 0.30
        + calibrated["dhash"] * 0.20
    )

    temporal_raw = _aligned_vector_similarity(
        fp1.get("temporal_signature", []),
        fp2.get("temporal_signature", []),
        primary_pairs,
    )
    color_raw = _aligned_vector_similarity(
        fp1.get("histograms", []),
        fp2.get("histograms", []),
        primary_pairs,
    )
    motion2 = fp2.get("motion_signature", [])
    if primary_alignment.get("orientation") == "mirrored":
        motion2 = [_mirror_motion_vector(v) for v in motion2]
    motion_raw = _aligned_vector_similarity(
        fp1.get("motion_signature", []),
        motion2,
        primary_pairs,
    )

    # Colour and motion cosine scores have high unrelated-pair baselines. Remove
    # those baselines before fusion so common palettes or generic movement do
    # not look like content reuse. Temporal scalar sequences are compared as a
    # whole; comparing each positive scalar independently would always yield a
    # cosine score of 1.0.
    temporal = temporal_raw
    color = _remove_similarity_baseline(color_raw, COLOR_RANDOM_BASELINE)
    motion = _remove_similarity_baseline(motion_raw, MOTION_RANDOM_BASELINE)

    # Supporting signals are useful only after the frame structure shows a
    # credible relationship. This gate prevents unrelated videos with similar
    # colours, pacing, or movement from accumulating a misleading total.
    overall, support_gate = _fuse_scores(perceptual, temporal, color, motion)

    # The base weights remain visible in the output, but the three supporting
    # channels only contribute to the total when the frame hashes establish a
    # credible structural relationship.

    d1 = float(fp1.get("duration") or 0.0)
    d2 = float(fp2.get("duration") or 0.0)
    duration_similarity = min(d1, d2) / max(d1, d2) if d1 > 0 and d2 > 0 else 0.0
    matched_frames = len(primary_pairs)
    longer_length = max(len(hashes1), len(hashes2))
    longer_index = 1 if len(hashes2) > len(hashes1) else 0
    longer_indices = [pair[longer_index] for pair in primary_pairs]
    longer_coverage = len(set(longer_indices)) / longer_length if longer_length else 0.0

    return {
        "overall": _percent(overall),
        "perceptual": _percent(perceptual),
        "phash": _percent(calibrated["phash"]),
        "whash": _percent(calibrated["whash"]),
        "dhash": _percent(calibrated["dhash"]),
        "ahash": _percent(calibrated["ahash"]),
        "temporal": _percent(temporal * support_gate),
        "color": _percent(color * support_gate),
        "motion": _percent(motion * support_gate),
        "duration": _percent(duration_similarity),
        "support_gate": _percent(support_gate),
        "diagnostics": {
            "aligned_temporal_before_gate": _percent(temporal),
            "aligned_color_before_gate": _percent(color),
            "aligned_motion_before_gate": _percent(motion),
        },
        "alignment": {
            "orientation": primary_alignment.get("orientation", "normal"),
            "time_scale": round(float(primary_alignment.get("time_scale", 1.0)), 3),
            "offset_frames": int(primary_alignment.get("offset", 0)),
            "matched_frames": matched_frames,
            "longer_video_coverage": _percent(longer_coverage),
        },
        "weights": {key: int(value * 100) for key, value in FUSION_WEIGHTS.items()},
        "scoring_note": (
            "Frame-hash scores remove the unrelated-pair Hamming baseline. "
            "Colour, temporal, and motion evidence use the same pHash alignment "
            "and are reduced when structural similarity is weak."
        ),
    }


def _sample_indices(
    total_frames: int,
    fps: float,
    sample_fps: float,
    max_samples: int,
) -> list[int]:
    if total_frames <= 0:
        return [0]
    if fps <= 0:
        fps = 30.0
    step = max(1, int(round(fps / sample_fps)))
    indices = list(range(0, total_frames, step))
    if total_frames > 1 and indices[-1] != total_frames - 1:
        indices.append(total_frames - 1)
    if len(indices) > max_samples:
        indices = np.linspace(0, total_frames - 1, max_samples).round().astype(int).tolist()
    return sorted(set(indices))


def _frame_hashes(image: Image.Image) -> dict[str, imagehash.ImageHash]:
    return {
        "phash": imagehash.phash(image),
        "whash": imagehash.whash(image),
        "dhash": imagehash.dhash(image),
        "ahash": imagehash.average_hash(image),
    }


def _rgb_histogram(rgb: np.ndarray) -> list[float]:
    channels = []
    for channel in range(3):
        hist = cv2.calcHist([rgb], [channel], None, [32], [0, 256]).flatten()
        total = float(hist.sum())
        channels.append(hist / total if total > 0 else hist)
    return np.concatenate(channels).astype(np.float32).tolist()


def _motion_vector(previous: np.ndarray, current: np.ndarray) -> list[float]:
    diff = cv2.absdiff(previous, current).astype(np.float32) / 255.0
    magnitude = float(diff.mean())
    mass = float(diff.sum())
    if mass <= 1e-8 or magnitude <= 1e-6:
        return [0.0, 0.0, 0.0]
    yy, xx = np.indices(diff.shape, dtype=np.float32)
    cx = float((xx * diff).sum() / mass) / max(1.0, diff.shape[1] - 1)
    cy = float((yy * diff).sum() / mass) / max(1.0, diff.shape[0] - 1)
    return [magnitude, cx - 0.5, cy - 0.5]


def _mirror_motion_vector(vector: list[float]) -> list[float]:
    if len(vector) < 3:
        return vector
    return [float(vector[0]), -float(vector[1]), float(vector[2])]


def _best_hash_alignment(
    sequence1: list[dict[str, str]],
    sequence2: list[dict[str, str]],
    key: str,
) -> tuple[float, dict[str, Any]]:
    if not sequence1 or not sequence2:
        return 0.0, {"offset": 0, "time_scale": 1.0}
    best_score = 0.0
    best_alignment: dict[str, Any] = {"offset": 0, "time_scale": 1.0}
    for pairs, alignment in _candidate_alignments(len(sequence1), len(sequence2)):
        similarities = []
        for index1, index2 in pairs:
            try:
                hash1 = imagehash.hex_to_hash(sequence1[index1][key])
                hash2 = imagehash.hex_to_hash(sequence2[index2][key])
                similarities.append(max(0.0, 1.0 - float(hash1 - hash2) / 64.0))
            except (KeyError, TypeError, ValueError):
                continue
        score = float(np.mean(similarities)) if similarities else 0.0
        if score > best_score:
            best_score = score
            best_alignment = {**alignment, "_pairs": pairs}
    return best_score, best_alignment


def _hash_similarity_for_pairs(
    sequence1: list[dict[str, str]],
    sequence2: list[dict[str, str]],
    key: str,
    pairs: list[tuple[int, int]],
) -> float:
    similarities: list[float] = []
    for index1, index2 in pairs:
        if index1 >= len(sequence1) or index2 >= len(sequence2):
            continue
        try:
            hash1 = imagehash.hex_to_hash(sequence1[index1][key])
            hash2 = imagehash.hex_to_hash(sequence2[index2][key])
            similarities.append(max(0.0, 1.0 - float(hash1 - hash2) / 64.0))
        except (KeyError, TypeError, ValueError):
            continue
    return float(np.mean(similarities)) if similarities else 0.0


def _aligned_vector_similarity(
    sequence1: list[Any],
    sequence2: list[Any],
    pairs: list[tuple[int, int]],
) -> float:
    aligned: list[tuple[np.ndarray, np.ndarray]] = []
    for index1, index2 in pairs:
        if index1 >= len(sequence1) or index2 >= len(sequence2):
            continue
        first = np.atleast_1d(np.asarray(sequence1[index1], dtype=np.float32))
        second = np.atleast_1d(np.asarray(sequence2[index2], dtype=np.float32))
        if first.size and second.size:
            aligned.append((first, second))
    if not aligned:
        return 0.0

    if all(first.size == 1 and second.size == 1 for first, second in aligned):
        first_values = np.asarray([float(first[0]) for first, _ in aligned], dtype=np.float32)
        second_values = np.asarray([float(second[0]) for _, second in aligned], dtype=np.float32)
        rmse = float(np.sqrt(np.mean(np.square(first_values - second_values))))
        level_similarity = max(0.0, 1.0 - rmse / 0.35)
        if len(aligned) >= 3 and float(first_values.std()) > 0.015 and float(second_values.std()) > 0.015:
            correlation = float(np.corrcoef(first_values, second_values)[0, 1])
            correlation_similarity = max(0.0, min(1.0, (correlation + 1.0) / 2.0))
            return 0.65 * level_similarity + 0.35 * correlation_similarity
        return level_similarity

    similarities = [_cosine(first, second) for first, second in aligned]
    return float(np.mean(similarities)) if similarities else 0.0


def _best_vector_alignment(
    sequence1: list[Any],
    sequence2: list[Any],
) -> tuple[float, dict[str, Any]]:
    if not sequence1 or not sequence2:
        return 0.0, {"offset": 0, "time_scale": 1.0}
    vectors1 = [np.atleast_1d(np.asarray(value, dtype=np.float32)) for value in sequence1]
    vectors2 = [np.atleast_1d(np.asarray(value, dtype=np.float32)) for value in sequence2]
    best_score = 0.0
    best_alignment: dict[str, Any] = {"offset": 0, "time_scale": 1.0}
    for pairs, alignment in _candidate_alignments(len(vectors1), len(vectors2)):
        similarities = [_cosine(vectors1[i], vectors2[j]) for i, j in pairs]
        score = float(np.mean(similarities)) if similarities else 0.0
        if score > best_score:
            best_score = score
            best_alignment = alignment
    return best_score, best_alignment


def _candidate_alignments(
    length1: int,
    length2: int,
    max_offsets: int = 24,
) -> list[tuple[list[tuple[int, int]], dict[str, Any]]]:
    """Build sliding-window alignments with modest speed-change tolerance."""
    if length1 <= 0 or length2 <= 0:
        return []

    first_is_reference = length1 <= length2
    reference_length = min(length1, length2)
    longer_length = max(length1, length2)
    scale_candidates = {0.75, 0.85, 1.0, 1.15, 1.25, 1.35}
    if longer_length / reference_length <= 1.6:
        scale_candidates.add(longer_length / reference_length)

    results = []
    seen: set[tuple[int, int]] = set()
    for scale in sorted(scale_candidates):
        window_length = min(longer_length, max(1, int(round(reference_length * scale))))
        max_start = longer_length - window_length
        starts = np.linspace(0, max_start, min(max_offsets, max_start + 1)).round().astype(int)
        for start_value in starts.tolist():
            start = int(start_value)
            marker = (start, window_length)
            if marker in seen:
                continue
            seen.add(marker)
            reference_indices = np.arange(reference_length, dtype=int)
            longer_indices = np.linspace(start, start + window_length - 1, reference_length).round().astype(int)
            if first_is_reference:
                pairs = list(zip(reference_indices.tolist(), longer_indices.tolist()))
            else:
                pairs = list(zip(longer_indices.tolist(), reference_indices.tolist()))
            results.append((pairs, {"offset": start, "time_scale": window_length / reference_length}))
    return results


def _cosine(vector1: np.ndarray, vector2: np.ndarray) -> float:
    size = min(vector1.size, vector2.size)
    if size == 0:
        return 0.0
    a = vector1[:size]
    b = vector2[:size]
    norm_a = float(np.linalg.norm(a))
    norm_b = float(np.linalg.norm(b))
    if norm_a <= 1e-12 and norm_b <= 1e-12:
        return 1.0
    if norm_a <= 1e-12 or norm_b <= 1e-12:
        return 0.0
    return max(0.0, min(1.0, float(np.dot(a, b) / (norm_a * norm_b))))


def _calibrate_hash(raw_similarity: float) -> float:
    return max(0.0, min(1.0, (raw_similarity - 0.5) * 2.0))


def _remove_similarity_baseline(raw_similarity: float, baseline: float) -> float:
    if baseline >= 1.0:
        return 0.0
    return max(0.0, min(1.0, (raw_similarity - baseline) / (1.0 - baseline)))


def _linear_gate(value: float, floor: float, full: float) -> float:
    if full <= floor:
        return 1.0 if value >= full else 0.0
    return max(0.0, min(1.0, (value - floor) / (full - floor)))


def _fuse_scores(
    perceptual: float,
    temporal: float,
    color: float,
    motion: float,
) -> tuple[float, float]:
    """Fuse aligned evidence and return ``(score, support_gate)`` in 0-1 units."""
    support_gate = _linear_gate(perceptual, SUPPORT_GATE_FLOOR, SUPPORT_GATE_FULL)
    score = (
        perceptual * FUSION_WEIGHTS["perceptual"]
        + support_gate
        * (
            temporal * FUSION_WEIGHTS["temporal"]
            + color * FUSION_WEIGHTS["color"]
            + motion * FUSION_WEIGHTS["motion"]
        )
    )
    return score, support_gate


def _percent(value: float) -> float:
    return round(max(0.0, min(1.0, float(value))) * 100.0, 2)


def _fourcc_name(value: int) -> str:
    if value <= 0:
        return "unknown"
    name = "".join(chr((value >> (8 * index)) & 0xFF) for index in range(4))
    cleaned = "".join(character for character in name if character.isprintable()).strip()
    return cleaned or "unknown"
