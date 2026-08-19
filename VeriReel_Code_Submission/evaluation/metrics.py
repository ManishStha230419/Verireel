"""Dependency-light metrics used by the VeriReel evaluation harness."""

from __future__ import annotations

import random
from collections import defaultdict
from typing import Any, Iterable


def _divide(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def confusion_counts(labels: Iterable[int], predictions: Iterable[int]) -> dict[str, int]:
    counts = {"tp": 0, "fp": 0, "tn": 0, "fn": 0}
    for label, prediction in zip(labels, predictions):
        if int(label) == 1 and int(prediction) == 1:
            counts["tp"] += 1
        elif int(label) == 0 and int(prediction) == 1:
            counts["fp"] += 1
        elif int(label) == 0 and int(prediction) == 0:
            counts["tn"] += 1
        else:
            counts["fn"] += 1
    return counts


def metrics_from_counts(counts: dict[str, int]) -> dict[str, float | int]:
    tp = counts["tp"]
    fp = counts["fp"]
    tn = counts["tn"]
    fn = counts["fn"]
    precision = _divide(tp, tp + fp)
    recall = _divide(tp, tp + fn)
    specificity = _divide(tn, tn + fp)
    accuracy = _divide(tp + tn, tp + fp + tn + fn)
    f1 = _divide(2 * precision * recall, precision + recall)
    return {
        **counts,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "specificity": specificity,
        "accuracy": accuracy,
        "balanced_accuracy": (recall + specificity) / 2.0,
    }


def classification_metrics(
    records: list[dict[str, Any]],
    *,
    threshold: float | None = None,
    score_key: str = "score",
    prediction_key: str | None = None,
) -> dict[str, float | int]:
    labels = [int(record["label"]) for record in records]
    if prediction_key is not None:
        predictions = [int(record[prediction_key]) for record in records]
    elif threshold is not None:
        predictions = [int(float(record[score_key]) >= threshold) for record in records]
    else:
        raise ValueError("Either threshold or prediction_key is required.")
    return metrics_from_counts(confusion_counts(labels, predictions))


def average_precision(records: list[dict[str, Any]], score_key: str = "score") -> float:
    ordered = sorted(records, key=lambda record: float(record[score_key]), reverse=True)
    positives = sum(int(record["label"]) for record in ordered)
    if positives == 0:
        return 0.0
    hits = 0
    accumulated_precision = 0.0
    for rank, record in enumerate(ordered, 1):
        if int(record["label"]) == 1:
            hits += 1
            accumulated_precision += hits / rank
    return accumulated_precision / positives


def threshold_curve(
    records: list[dict[str, Any]],
    thresholds: Iterable[float],
    score_key: str = "score",
) -> list[dict[str, float | int]]:
    curve = []
    for threshold in thresholds:
        metrics = classification_metrics(records, threshold=threshold, score_key=score_key)
        curve.append({"threshold": float(threshold), **metrics})
    return curve


def recall_by_group(
    records: list[dict[str, Any]],
    *,
    group_key: str,
    threshold: float | None = None,
    score_key: str = "score",
    prediction_key: str | None = None,
) -> dict[str, dict[str, float | int]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        if int(record["label"]) == 1:
            groups[str(record[group_key])].append(record)
    result = {}
    for group, rows in sorted(groups.items()):
        metrics = classification_metrics(
            rows,
            threshold=threshold,
            score_key=score_key,
            prediction_key=prediction_key,
        )
        result[group] = {
            "positive_pairs": len(rows),
            "true_positives": metrics["tp"],
            "false_negatives": metrics["fn"],
            "recall": metrics["recall"],
        }
    return result


def bootstrap_intervals(
    records: list[dict[str, Any]],
    *,
    threshold: float,
    score_key: str = "score",
    iterations: int = 1000,
    seed: int = 20260815,
) -> dict[str, dict[str, float]]:
    if not records:
        return {}
    # The seeded generator is used only for reproducible statistical bootstrap
    # resampling; it never creates tokens, secrets, or other security values.
    rng = random.Random(seed)  # nosec B311
    samples = {"precision": [], "recall": [], "f1": []}
    for _ in range(iterations):
        resample = [records[rng.randrange(len(records))] for _ in records]
        metrics = classification_metrics(resample, threshold=threshold, score_key=score_key)
        for name in samples:
            samples[name].append(float(metrics[name]))

    intervals = {}
    for name, values in samples.items():
        values.sort()
        lower = values[int(0.025 * (len(values) - 1))]
        upper = values[int(0.975 * (len(values) - 1))]
        intervals[name] = {"lower_95": lower, "upper_95": upper}
    return intervals


def rounded(value: Any, digits: int = 4) -> Any:
    if isinstance(value, float):
        return round(value, digits)
    if isinstance(value, dict):
        return {key: rounded(item, digits) for key, item in value.items()}
    if isinstance(value, list):
        return [rounded(item, digits) for item in value]
    return value
