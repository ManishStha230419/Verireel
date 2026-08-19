"""Generate a deterministic, non-sensitive controlled video validation set."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path

import cv2
import numpy as np


SEED = 20260815
WIDTH = 112
HEIGHT = 192
FPS = 8
FRAMES = 48
BASE_CLIPS = 18
TRANSFORMATIONS = (
    "mirror",
    "crop_resize",
    "recompression",
    "caption_overlay",
    "brightness_shift",
    "speed_1_25x",
)


def _writer(path: Path, fps: int = FPS) -> cv2.VideoWriter:
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(
        str(path),
        cv2.VideoWriter_fourcc(*"MJPG"),
        fps,
        (WIDTH, HEIGHT),
    )
    if not writer.isOpened():
        raise RuntimeError(f"OpenCV could not create {path}")
    return writer


def _base_frame(clip_index: int, frame_index: int) -> np.ndarray:
    rng = np.random.default_rng(SEED + clip_index)
    yy, xx = np.indices((HEIGHT, WIDTH))
    phase = clip_index * 0.73
    channel_a = (35 + 35 * np.sin(xx / (6 + clip_index % 5) + phase)).astype(np.int16)
    channel_b = (45 + 30 * np.cos(yy / (8 + clip_index % 4) + phase)).astype(np.int16)
    channel_c = (30 + ((xx * (clip_index + 3) + yy * (clip_index + 5)) % 70)).astype(np.int16)
    frame = np.stack((channel_a, channel_b, channel_c), axis=2)
    tint = np.array(
        [
            (clip_index * 37) % 115,
            (clip_index * 61) % 105,
            (clip_index * 83) % 95,
        ],
        dtype=np.int16,
    )
    frame = np.clip(frame + tint, 0, 255).astype(np.uint8)

    for shape_index in range(4):
        base_x = int(rng.integers(10, WIDTH - 25))
        base_y = int(rng.integers(25, HEIGHT - 35))
        dx = int(10 * math.sin(frame_index / (4.0 + shape_index) + shape_index + phase))
        dy = int(12 * math.cos(frame_index / (5.0 + shape_index) + phase))
        color = tuple(int(value) for value in rng.integers(60, 245, size=3))
        if (clip_index + shape_index) % 3 == 0:
            cv2.circle(frame, (base_x + dx, base_y + dy), 6 + shape_index * 2, color, -1)
        elif (clip_index + shape_index) % 3 == 1:
            cv2.rectangle(
                frame,
                (base_x + dx - 7, base_y + dy - 8),
                (base_x + dx + 9, base_y + dy + 11),
                color,
                -1,
            )
        else:
            points = np.array(
                [
                    [base_x + dx, base_y + dy - 10],
                    [base_x + dx - 9, base_y + dy + 8],
                    [base_x + dx + 10, base_y + dy + 7],
                ],
                dtype=np.int32,
            )
            cv2.fillPoly(frame, [points], color)

    cv2.putText(
        frame,
        f"VR-{clip_index + 1:02d}",
        (7, HEIGHT - 12),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.46,
        (245, 245, 245),
        1,
        cv2.LINE_AA,
    )
    progress_x = 5 + int((WIDTH - 10) * frame_index / max(1, FRAMES - 1))
    cv2.line(frame, (5, 12), (progress_x, 12), (240, 210, 70), 3)
    return frame


def _write_base(path: Path, clip_index: int) -> None:
    writer = _writer(path)
    try:
        for frame_index in range(FRAMES):
            writer.write(_base_frame(clip_index, frame_index))
    finally:
        writer.release()


def _read_frames(path: Path) -> list[np.ndarray]:
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise RuntimeError(f"Could not read {path}")
    frames = []
    try:
        while True:
            ok, frame = capture.read()
            if not ok or frame is None:
                break
            frames.append(frame)
    finally:
        capture.release()
    if not frames:
        raise RuntimeError(f"No frames decoded from {path}")
    return frames


def _transform_frame(frame: np.ndarray, name: str, frame_index: int) -> np.ndarray:
    if name == "mirror":
        return cv2.flip(frame, 1)
    if name == "crop_resize":
        crop_x = 12
        crop_y = 17
        cropped = frame[crop_y : HEIGHT - crop_y, crop_x : WIDTH - crop_x]
        return cv2.resize(cropped, (WIDTH, HEIGHT), interpolation=cv2.INTER_LINEAR)
    if name == "recompression":
        ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 28])
        if not ok:
            raise RuntimeError("JPEG recompression failed")
        return cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    if name == "caption_overlay":
        output = frame.copy()
        cv2.rectangle(output, (0, HEIGHT - 44), (WIDTH, HEIGHT), (10, 10, 10), -1)
        cv2.putText(
            output,
            "CREATOR NOTE",
            (6, HEIGHT - 24),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.34,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )
        cv2.putText(
            output,
            f"frame {frame_index + 1}",
            (6, HEIGHT - 9),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.31,
            (205, 205, 205),
            1,
            cv2.LINE_AA,
        )
        return output
    if name == "brightness_shift":
        return cv2.convertScaleAbs(frame, alpha=0.78, beta=42)
    return frame


def _write_transform(source: Path, target: Path, name: str) -> None:
    frames = _read_frames(source)
    if name == "speed_1_25x":
        output_count = max(1, int(round(len(frames) / 1.25)))
        indices = np.linspace(0, len(frames) - 1, output_count).round().astype(int).tolist()
        output_frames = [frames[index] for index in indices]
    else:
        output_frames = [
            _transform_frame(frame, name, index)
            for index, frame in enumerate(frames)
        ]

    writer = _writer(target)
    try:
        for frame in output_frames:
            writer.write(frame)
    finally:
        writer.release()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def generate(output_dir: Path, force: bool = False) -> Path:
    videos_dir = output_dir / "videos"
    manifest_path = output_dir / "manifest.csv"
    output_dir.mkdir(parents=True, exist_ok=True)

    bases = []
    variants: dict[tuple[int, str], Path] = {}
    for clip_index in range(BASE_CLIPS):
        base_path = videos_dir / f"base_{clip_index + 1:02d}.avi"
        if force or not base_path.exists():
            _write_base(base_path, clip_index)
        bases.append(base_path)
        for transformation in TRANSFORMATIONS:
            variant_path = videos_dir / f"base_{clip_index + 1:02d}__{transformation}.avi"
            if force or not variant_path.exists():
                _write_transform(base_path, variant_path, transformation)
            variants[(clip_index, transformation)] = variant_path

    rows = []
    pair_number = 1
    for transformation_index, transformation in enumerate(TRANSFORMATIONS):
        for clip_index in range(BASE_CLIPS):
            positive_variant = variants[(clip_index, transformation)]
            rows.append(
                {
                    "pair_id": f"P{pair_number:04d}",
                    "video_1": bases[clip_index].relative_to(output_dir).as_posix(),
                    "video_2": positive_variant.relative_to(output_dir).as_posix(),
                    "label": 1,
                    "pair_kind": "transformed_reuse",
                    "transformation": transformation,
                    "split": "frozen_test",
                }
            )
            pair_number += 1

            unrelated_index = (clip_index + 1 + transformation_index * 3) % BASE_CLIPS
            negative_variant = variants[(unrelated_index, transformation)]
            rows.append(
                {
                    "pair_id": f"N{pair_number:04d}",
                    "video_1": bases[clip_index].relative_to(output_dir).as_posix(),
                    "video_2": negative_variant.relative_to(output_dir).as_posix(),
                    "label": 0,
                    "pair_kind": "unrelated",
                    "transformation": transformation,
                    "split": "frozen_test",
                }
            )
            pair_number += 1

    with manifest_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    provenance = {
        "name": "VeriReel controlled pilot validation dataset",
        "scope": "Synthetic, non-sensitive proof-of-function; not an official effectiveness benchmark.",
        "seed": SEED,
        "base_clips": BASE_CLIPS,
        "frames_per_base_clip": FRAMES,
        "fps": FPS,
        "resolution": {"width": WIDTH, "height": HEIGHT},
        "transformations": list(TRANSFORMATIONS),
        "positive_pairs": sum(int(row["label"]) for row in rows),
        "negative_pairs": sum(1 - int(row["label"]) for row in rows),
        "manifest_sha256": _sha256(manifest_path),
    }
    (output_dir / "provenance.json").write_text(
        json.dumps(provenance, indent=2),
        encoding="utf-8",
    )
    return manifest_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).parent / "data")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    manifest = generate(args.output_dir.resolve(), force=args.force)
    print(f"Generated controlled dataset: {manifest}")


if __name__ == "__main__":
    main()

