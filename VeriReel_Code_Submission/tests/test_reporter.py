import unittest

from utils.reporter import generate_report


class ReporterTests(unittest.TestCase):
    def setUp(self):
        self.similarity = {
            "overall": 82.0,
            "perceptual": 86.0,
            "temporal": 78.0,
            "color": 80.0,
            "motion": 72.0,
            "alignment": {"orientation": "mirrored", "time_scale": 1.15},
        }

    def test_high_score_is_candidate_not_legal_verdict(self):
        report = generate_report(
            self.similarity,
            {"timestamp": 100, "author": "A"},
            {"timestamp": 200, "author": "B"},
            threshold=75,
        )
        self.assertEqual(report["verdict"], "MATCH_CANDIDATE")
        self.assertEqual(report["original_video"], "video1")
        self.assertTrue(report["human_review_required"])
        self.assertFalse(report["automated_enforcement_recommended"])
        self.assertIn("not a legal finding", report["analysis"])

    def test_missing_dates_do_not_infer_original(self):
        report = generate_report(self.similarity, {"timestamp": 0}, {"timestamp": 200})
        self.assertEqual(report["original_video"], "unknown")


if __name__ == "__main__":
    unittest.main()
