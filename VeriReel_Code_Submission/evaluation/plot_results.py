"""Create publication-ready PNG figures from VeriReel pilot results."""

from __future__ import annotations

import argparse
import csv
import json
import math
import textwrap
from pathlib import Path
from typing import Any

import cv2
from PIL import Image, ImageDraw, ImageFont


WIDTH = 1600
HEIGHT = 1000
INK = "#172033"
MUTED = "#627086"
PURPLE = "#5B55D6"
PURPLE_LIGHT = "#E9E8FF"
TEAL = "#0D9488"
GOLD = "#D69715"
RED = "#C94A55"
GREEN = "#16835D"
GRID = "#DCE2EA"
PALE = "#F5F7FA"
WHITE = "#FFFFFF"


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
    )
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _canvas(title: str, subtitle: str) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", (WIDTH, HEIGHT), WHITE)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, WIDTH, 22), fill=PURPLE)
    draw.text((80, 64), title, fill=INK, font=_font(42, True))
    draw.text((80, 121), subtitle, fill=MUTED, font=_font(22))
    draw.line((80, 166, WIDTH - 80, 166), fill=GRID, width=3)
    return image, draw


def _save(image: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PNG", optimize=True, dpi=(180, 180))


def _percent(value: float) -> str:
    return f"{100.0 * float(value):.1f}%"


def transformation_examples(data_dir: Path, output: Path) -> None:
    image, draw = _canvas(
        "Controlled transformation examples",
        "One synthetic source clip and six deterministic transformations",
    )
    items = [
        ("Original", data_dir / "videos" / "base_01.avi"),
        ("Mirrored", data_dir / "videos" / "base_01__mirror.avi"),
        ("Crop + resize", data_dir / "videos" / "base_01__crop_resize.avi"),
        ("Recompressed", data_dir / "videos" / "base_01__recompression.avi"),
        ("Caption overlay", data_dir / "videos" / "base_01__caption_overlay.avi"),
        ("Brightness shift", data_dir / "videos" / "base_01__brightness_shift.avi"),
        ("Speed 1.25x", data_dir / "videos" / "base_01__speed_1_25x.avi"),
    ]

    def frame(path: Path) -> Image.Image:
        capture = cv2.VideoCapture(str(path))
        try:
            count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 1)
            capture.set(cv2.CAP_PROP_POS_FRAMES, max(0, count // 2))
            ok, value = capture.read()
            if not ok or value is None:
                raise RuntimeError(f"Could not read preview frame from {path}")
            rgb = cv2.cvtColor(value, cv2.COLOR_BGR2RGB)
            return Image.fromarray(rgb)
        finally:
            capture.release()

    panel_w, panel_h = 205, 320
    gap_x, gap_y = 55, 40
    start_x, start_y = 230, 205
    positions = [
        (start_x + column * (panel_w + gap_x), start_y + row * (panel_h + gap_y))
        for row in range(2)
        for column in range(4)
    ]
    for (label, path), (x, y) in zip(items, positions):
        preview = frame(path)
        preview.thumbnail((panel_w, 260), Image.Resampling.LANCZOS)
        px = x + (panel_w - preview.width) // 2
        draw.rounded_rectangle((x - 10, y - 10, x + panel_w + 10, y + panel_h), radius=18, fill=PALE, outline=GRID, width=2)
        image.paste(preview, (px, y))
        draw.text((x + panel_w / 2, y + 286), label, fill=INK, font=_font(18, True), anchor="mm")
    draw.text((80, 948), "All clips are generated, non-sensitive and reproducible from seed 20260815.", fill=MUTED, font=_font(18))
    _save(image, output)


def validation_summary(metrics: dict[str, Any], output: Path) -> None:
    image, draw = _canvas(
        "Controlled pilot validation at the frozen 75% threshold",
        "Measured results on deterministic transformed and unrelated synthetic video pairs",
    )
    dataset = metrics["dataset"]
    model = metrics["verireel"]["metrics"]
    baseline = metrics["baseline"]["metrics"]

    cards = [
        ("Pairs evaluated", str(dataset["completed_pairs"]), f"{dataset['positive_pairs']} positive / {dataset['negative_pairs']} negative"),
        ("VeriReel precision", _percent(model["precision"]), f"TP {model['tp']}  |  FP {model['fp']}"),
        ("VeriReel recall", _percent(model["recall"]), f"TP {model['tp']}  |  FN {model['fn']}"),
        ("VeriReel F1", _percent(model["f1"]), f"AP {_percent(model['average_precision'])}"),
    ]
    card_width = 330
    gap = 30
    start_x = 80
    for index, (label, value, note) in enumerate(cards):
        x1 = start_x + index * (card_width + gap)
        x2 = x1 + card_width
        draw.rounded_rectangle((x1, 220, x2, 430), radius=22, fill=PALE, outline=GRID, width=2)
        draw.text((x1 + 24, 250), label.upper(), fill=MUTED, font=_font(17, True))
        draw.text((x1 + 24, 295), value, fill=PURPLE if index else INK, font=_font(49, True))
        draw.text((x1 + 24, 378), note, fill=MUTED, font=_font(18))

    draw.text((80, 500), "Comparison with the promised single-pHash baseline", fill=INK, font=_font(28, True))
    rows = [
        ("Precision", model["precision"], baseline["precision"]),
        ("Recall", model["recall"], baseline["recall"]),
        ("F1", model["f1"], baseline["f1"]),
        ("Average precision", model["average_precision"], baseline["average_precision"]),
    ]
    y = 565
    for label, model_value, baseline_value in rows:
        draw.text((90, y + 8), label, fill=INK, font=_font(20, True))
        track_left, track_right = 370, 1450
        draw.rounded_rectangle((track_left, y, track_right, y + 30), radius=15, fill="#EDF0F5")
        model_right = track_left + int((track_right - track_left) * float(model_value))
        baseline_right = track_left + int((track_right - track_left) * float(baseline_value))
        draw.rounded_rectangle((track_left, y, model_right, y + 13), radius=6, fill=PURPLE)
        draw.rounded_rectangle((track_left, y + 17, baseline_right, y + 30), radius=6, fill=TEAL)
        draw.text((1460, y - 3), _percent(model_value), fill=PURPLE, font=_font(17, True))
        draw.text((1460, y + 16), _percent(baseline_value), fill=TEAL, font=_font(17, True))
        y += 78

    draw.rectangle((80, 895, 112, 919), fill=PURPLE)
    draw.text((126, 897), "VeriReel", fill=MUTED, font=_font(18))
    draw.rectangle((270, 895, 302, 919), fill=TEAL)
    draw.text((316, 897), "single-pHash baseline", fill=MUTED, font=_font(18))
    draw.text((80, 948), "Controlled proof-of-function only; no real-world benchmark claim is made.", fill=RED, font=_font(18, True))
    _save(image, output)


def confusion_matrix_figure(metrics: dict[str, Any], output: Path) -> None:
    image, draw = _canvas(
        "Frozen-threshold confusion matrix",
        "Rows are ground truth; columns are match-candidate predictions at 75%",
    )
    values = metrics["verireel"]["metrics"]
    left, top, cell = 430, 270, 260
    labels = (("True negative", values["tn"]), ("False positive", values["fp"]), ("False negative", values["fn"]), ("True positive", values["tp"]))
    fills = ("#DDF4EA", "#FCE2E4", "#FFF0D5", "#E5E4FF")
    for index, ((label, value), fill) in enumerate(zip(labels, fills)):
        row, col = divmod(index, 2)
        x1, y1 = left + col * cell, top + row * cell
        draw.rounded_rectangle((x1, y1, x1 + cell - 18, y1 + cell - 18), radius=18, fill=fill, outline=GRID, width=2)
        draw.text((x1 + 28, y1 + 35), label, fill=MUTED, font=_font(20, True))
        draw.text((x1 + 28, y1 + 95), str(value), fill=INK, font=_font(72, True))
    draw.text((left + (cell - 18) / 2, 222), "Predicted: no match", fill=INK, font=_font(19, True), anchor="mm")
    draw.text((left + cell + (cell - 18) / 2, 222), "Predicted: match", fill=INK, font=_font(19, True), anchor="mm")
    draw.text((120, top + 85), "Actual:\nunrelated", fill=INK, font=_font(23, True), spacing=8)
    draw.text((120, top + cell + 80), "Actual:\ntransformed reuse", fill=INK, font=_font(23, True), spacing=8)
    draw.text((1060, 285), "Primary metrics", fill=INK, font=_font(29, True))
    metric_rows = [
        ("Precision", values["precision"]),
        ("Recall", values["recall"]),
        ("F1", values["f1"]),
        ("Specificity", values["specificity"]),
        ("Accuracy", values["accuracy"]),
    ]
    y = 350
    for label, value in metric_rows:
        draw.text((1070, y), label, fill=MUTED, font=_font(21))
        draw.text((1370, y - 3), _percent(value), fill=PURPLE, font=_font(25, True), anchor="ra")
        draw.line((1070, y + 38, 1420, y + 38), fill=GRID, width=2)
        y += 79
    draw.text((80, 948), "Dataset: controlled synthetic frozen test split; n = " + str(metrics["dataset"]["completed_pairs"]), fill=MUTED, font=_font(18))
    _save(image, output)


def threshold_tradeoff(metrics: dict[str, Any], output: Path) -> None:
    image, draw = _canvas(
        "Precision-recall trade-off across decision thresholds",
        "The registered operating point is 75%; other thresholds are shown for interpretation, not tuning",
    )
    curve = metrics["threshold_curve"]
    left, right, top, bottom = 150, 1490, 235, 820
    draw.line((left, bottom, right, bottom), fill=INK, width=3)
    draw.line((left, top, left, bottom), fill=INK, width=3)
    for tick in range(0, 11):
        value = tick / 10
        y = bottom - int(value * (bottom - top))
        draw.line((left, y, right, y), fill=GRID, width=1)
        draw.text((left - 20, y), f"{value:.1f}", fill=MUTED, font=_font(17), anchor="rm")
    for threshold in range(50, 96, 5):
        x = left + int((threshold - 50) / 45 * (right - left))
        draw.line((x, bottom, x, bottom + 10), fill=INK, width=2)
        draw.text((x, bottom + 22), str(threshold), fill=MUTED, font=_font(17), anchor="ma")
    draw.text(((left + right) // 2, 910), "Decision threshold (%)", fill=INK, font=_font(21, True), anchor="mm")
    draw.text((55, (top + bottom) // 2), "Metric", fill=INK, font=_font(21, True), anchor="mm")

    for key, color in (("precision", PURPLE), ("recall", TEAL), ("f1", GOLD)):
        points = []
        for row in curve:
            x = left + int((float(row["threshold"]) - 50) / 45 * (right - left))
            y = bottom - int(float(row[key]) * (bottom - top))
            points.append((x, y))
        draw.line(points, fill=color, width=7, joint="curve")

    frozen_x = left + int((75 - 50) / 45 * (right - left))
    draw.line((frozen_x, top, frozen_x, bottom), fill=RED, width=3)
    draw.text((frozen_x + 12, top + 8), "Frozen 75%", fill=RED, font=_font(19, True))
    legend_y = 190
    for x, label, color in ((930, "Precision", PURPLE), (1115, "Recall", TEAL), (1265, "F1", GOLD)):
        draw.line((x, legend_y, x + 45, legend_y), fill=color, width=7)
        draw.text((x + 58, legend_y), label, fill=MUTED, font=_font(18), anchor="lm")
    _save(image, output)


def transformation_recall(metrics: dict[str, Any], output: Path) -> None:
    image, draw = _canvas(
        "Recall by transformation family",
        "VeriReel at 75% compared with the single-pHash/Hamming-10 baseline",
    )
    model = metrics["verireel"]["per_transformation"]
    baseline = metrics["baseline"]["per_transformation"]
    transformations = sorted(model)
    left, right, top, bottom = 310, 1490, 230, 845
    draw.line((left, top, left, bottom), fill=INK, width=3)
    for tick in range(0, 11, 2):
        value = tick / 10
        x = left + int(value * (right - left))
        draw.line((x, top, x, bottom), fill=GRID, width=1)
        draw.text((x, bottom + 22), f"{int(value * 100)}%", fill=MUTED, font=_font(17), anchor="ma")
    band = (bottom - top) / len(transformations)
    for index, transformation in enumerate(transformations):
        y = top + index * band + band / 2
        label = transformation.replace("_", " ").replace("1 25x", "1.25x")
        draw.text((left - 28, y), label, fill=INK, font=_font(20, True), anchor="rm")
        model_value = float(model[transformation]["recall"])
        baseline_value = float(baseline[transformation]["recall"])
        model_right = left + int(model_value * (right - left))
        baseline_right = left + int(baseline_value * (right - left))
        draw.rounded_rectangle((left, y - 24, model_right, y - 3), radius=9, fill=PURPLE)
        draw.rounded_rectangle((left, y + 5, baseline_right, y + 26), radius=9, fill=TEAL)
        if model_value >= 0.9:
            draw.text((right - 12, y - 14), _percent(model_value), fill=WHITE, font=_font(16, True), anchor="rm")
        else:
            draw.text((model_right + 12, y - 14), _percent(model_value), fill=PURPLE, font=_font(16, True), anchor="lm")
        if baseline_value >= 0.9:
            draw.text((right - 12, y + 16), _percent(baseline_value), fill=WHITE, font=_font(16, True), anchor="rm")
        else:
            draw.text((baseline_right + 12, y + 16), _percent(baseline_value), fill=TEAL, font=_font(16, True), anchor="lm")
    draw.rectangle((80, 910, 112, 934), fill=PURPLE)
    draw.text((126, 912), "VeriReel", fill=MUTED, font=_font(18))
    draw.rectangle((270, 910, 302, 934), fill=TEAL)
    draw.text((316, 912), "single-pHash baseline", fill=MUTED, font=_font(18))
    _save(image, output)


def score_distribution(metrics: dict[str, Any], predictions: list[dict[str, str]], output: Path) -> None:
    image, draw = _canvas(
        "Score distribution for related and unrelated pairs",
        "Each bar shows the number of pairs in a five-point similarity-score bin",
    )
    bins = list(range(0, 101, 5))
    counts = {0: [0] * (len(bins) - 1), 1: [0] * (len(bins) - 1)}
    for record in predictions:
        score = min(99.999, max(0.0, float(record["overall_score"])))
        index = min(len(counts[0]) - 1, int(score // 5))
        counts[int(record["label"])][index] += 1
    max_count = max(max(counts[0]), max(counts[1]), 1)
    left, right, top, bottom = 150, 1490, 245, 820
    draw.line((left, bottom, right, bottom), fill=INK, width=3)
    draw.line((left, top, left, bottom), fill=INK, width=3)
    for tick in range(0, max_count + 1, max(1, math.ceil(max_count / 5))):
        y = bottom - int(tick / max_count * (bottom - top))
        draw.line((left, y, right, y), fill=GRID, width=1)
        draw.text((left - 18, y), str(tick), fill=MUTED, font=_font(16), anchor="rm")
    bin_width = (right - left) / len(counts[0])
    for index in range(len(counts[0])):
        x1 = left + index * bin_width
        for label, color, offset in ((0, RED, 0.08), (1, PURPLE, 0.51)):
            value = counts[label][index]
            bar_height = value / max_count * (bottom - top)
            bx1 = x1 + bin_width * offset
            bx2 = bx1 + bin_width * 0.39
            draw.rectangle((bx1, bottom - bar_height, bx2, bottom), fill=color)
    for score in range(0, 101, 10):
        x = left + score / 100 * (right - left)
        draw.text((x, bottom + 24), str(score), fill=MUTED, font=_font(17), anchor="ma")
    threshold_x = left + 0.75 * (right - left)
    draw.line((threshold_x, top, threshold_x, bottom), fill=GOLD, width=4)
    draw.text((threshold_x + 12, top + 8), "75% threshold", fill=GOLD, font=_font(19, True))
    draw.text(((left + right) // 2, 910), "Overall similarity score (%)", fill=INK, font=_font(21, True), anchor="mm")
    draw.text((60, (top + bottom) // 2), "Pair count", fill=INK, font=_font(20, True), anchor="mm")
    draw.rectangle((80, 190, 112, 214), fill=PURPLE)
    draw.text((126, 202), "Transformed reuse", fill=MUTED, font=_font(18), anchor="lm")
    draw.rectangle((340, 190, 372, 214), fill=RED)
    draw.text((386, 202), "Unrelated", fill=MUTED, font=_font(18), anchor="lm")
    _save(image, output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", type=Path, default=Path(__file__).parent / "results")
    args = parser.parse_args()
    results_dir = args.results_dir.resolve()
    metrics = json.loads((results_dir / "metrics.json").read_text(encoding="utf-8"))
    with (results_dir / "predictions.csv").open(newline="", encoding="utf-8") as handle:
        predictions = list(csv.DictReader(handle))
    figures = results_dir / "figures"
    transformation_examples(results_dir.parent / "data", figures / "figure_0_transformation_examples.png")
    validation_summary(metrics, figures / "figure_1_validation_summary.png")
    confusion_matrix_figure(metrics, figures / "figure_2_confusion_matrix.png")
    threshold_tradeoff(metrics, figures / "figure_3_threshold_tradeoff.png")
    transformation_recall(metrics, figures / "figure_4_transformation_recall.png")
    score_distribution(metrics, predictions, figures / "figure_5_score_distribution.png")
    print(f"Saved figures to {figures}")


if __name__ == "__main__":
    main()
