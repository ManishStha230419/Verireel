import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from utils.fingerprint import _fuse_scores, compare_videos, extract_fingerprint


def make_video(path: Path, variant: str = "reference") -> None:
    width, height, fps = 96, 160, 8
    writer = cv2.VideoWriter(
        str(path),
        cv2.VideoWriter_fourcc(*"MJPG"),
        fps,
        (width, height),
    )
    if not writer.isOpened():
        raise RuntimeError("OpenCV could not create the synthetic test video")
    for index in range(fps * 4):
        frame = np.zeros((height, width, 3), dtype=np.uint8)
        if variant == "different":
            frame[:] = (20, 110, 180)
            cv2.circle(frame, (48, 20 + index * 3 % 120), 15, (200, 30, 50), -1)
        else:
            frame[:] = (25 + index * 2, 30, 75)
            cv2.rectangle(frame, (8 + index * 2 % 60, 45), (35 + index * 2 % 60, 85), (40, 210, 130), -1)
            cv2.line(frame, (0, index * 5 % height), (width - 1, (index * 5 + 35) % height), (220, 180, 40), 3)
        if variant == "mirrored":
            frame = cv2.flip(frame, 1)
        writer.write(frame)
    writer.release()


class FingerprintTests(unittest.TestCase):
    def test_multisignal_features_and_identical_match(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "reference.avi"
            make_video(path)
            fingerprint = extract_fingerprint(path)
            result = compare_videos(fingerprint, fingerprint)

        self.assertEqual(len(fingerprint["histograms"][0]), 96)
        self.assertEqual(set(fingerprint["hashes"][0]), {"phash", "whash", "dhash", "ahash"})
        self.assertTrue(fingerprint["temporal_signature"])
        self.assertTrue(fingerprint["motion_signature"])
        self.assertGreater(result["overall"], 95)
        self.assertEqual(result["support_gate"], 100.0)
        self.assertEqual(result["weights"], {"perceptual": 45, "temporal": 25, "color": 20, "motion": 10})

    def test_mirror_alignment_outscores_different_content(self):
        with tempfile.TemporaryDirectory() as folder:
            reference_path = Path(folder) / "reference.avi"
            mirrored_path = Path(folder) / "mirrored.avi"
            different_path = Path(folder) / "different.avi"
            make_video(reference_path)
            make_video(mirrored_path, "mirrored")
            make_video(different_path, "different")

            reference = extract_fingerprint(reference_path)
            mirrored = compare_videos(reference, extract_fingerprint(mirrored_path))
            different = compare_videos(reference, extract_fingerprint(different_path))

        self.assertEqual(mirrored["alignment"]["orientation"], "mirrored")
        self.assertGreater(mirrored["overall"], different["overall"] + 10)
        self.assertLess(different["overall"], 20)

    def test_generic_supporting_signals_cannot_override_weak_frame_hashes(self):
        hash_a = "0000000000000000"
        hash_b = "ffffffff00000000"  # 32/64 bits differ: unrelated baseline.

        def fingerprint(value):
            hashes = [
                {"phash": value, "whash": value, "dhash": value, "ahash": value}
                for _ in range(12)
            ]
            return {
                "hashes": hashes,
                "flipped_hashes": hashes,
                "histograms": [[1.0] + [0.0] * 95 for _ in range(12)],
                "temporal_signature": [0.2] * 11,
                "motion_signature": [[0.1, 0.2, -0.1] for _ in range(11)],
                "duration": 12.0,
            }

        result = compare_videos(fingerprint(hash_a), fingerprint(hash_b))

        self.assertEqual(result["support_gate"], 0.0)
        self.assertEqual(result["temporal"], 0.0)
        self.assertEqual(result["color"], 0.0)
        self.assertEqual(result["motion"], 0.0)
        self.assertLess(result["overall"], 5.0)

    def test_reported_false_positive_pattern_is_reduced(self):
        # The user-observed case had 23.2% visual structure but very high
        # generic temporal, colour, and motion readings. Supporting evidence
        # must not turn that into a 60% candidate.
        score, gate = _fuse_scores(0.232, 1.0, 0.873, 0.715)

        self.assertEqual(gate, 0.0)
        self.assertAlmostEqual(score * 100.0, 10.44, places=2)


if __name__ == "__main__":
    unittest.main()
