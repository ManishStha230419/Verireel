import unittest

from evaluation.baselines import single_phash_baseline
from evaluation.metrics import average_precision, classification_metrics, recall_by_group


class EvaluationMetricTests(unittest.TestCase):
    def setUp(self):
        self.records = [
            {"label": 1, "score": 95.0, "group": "mirror"},
            {"label": 1, "score": 65.0, "group": "crop"},
            {"label": 0, "score": 80.0, "group": "mirror"},
            {"label": 0, "score": 20.0, "group": "crop"},
        ]

    def test_classification_metrics_use_frozen_threshold(self):
        metrics = classification_metrics(self.records, threshold=75.0)
        self.assertEqual((metrics["tp"], metrics["fp"], metrics["tn"], metrics["fn"]), (1, 1, 1, 1))
        self.assertEqual(metrics["precision"], 0.5)
        self.assertEqual(metrics["recall"], 0.5)
        self.assertEqual(metrics["f1"], 0.5)

    def test_average_precision_uses_full_score_ranking(self):
        self.assertAlmostEqual(average_precision(self.records), (1.0 + 2 / 3) / 2)

    def test_recall_is_broken_down_only_over_positive_pairs(self):
        breakdown = recall_by_group(self.records, group_key="group", threshold=75.0)
        self.assertEqual(breakdown["mirror"]["recall"], 1.0)
        self.assertEqual(breakdown["crop"]["recall"], 0.0)

    def test_single_phash_baseline_reports_mean_hamming(self):
        hashes = [{"phash": "0000000000000000"} for _ in range(4)]
        fingerprint = {"hashes": hashes}
        result = single_phash_baseline(fingerprint, fingerprint)
        self.assertEqual(result["mean_hamming"], 0.0)
        self.assertEqual(result["prediction"], 1)


if __name__ == "__main__":
    unittest.main()

