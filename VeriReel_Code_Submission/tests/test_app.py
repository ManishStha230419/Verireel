import io
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

import app as application


def video_bytes() -> bytes:
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "sample.avi"
        writer = cv2.VideoWriter(
            str(path),
            cv2.VideoWriter_fourcc(*"MJPG"),
            6,
            (72, 120),
        )
        if not writer.isOpened():
            raise RuntimeError("Could not create the API test video")
        for index in range(18):
            frame = np.full((120, 72, 3), (25, 35, 65), dtype=np.uint8)
            cv2.rectangle(frame, (8 + index, 34), (30 + index, 65), (60, 210, 140), -1)
            writer.write(frame)
        writer.release()
        return path.read_bytes()


def wait_for_job(client, job_id: str, headers: dict[str, str], timeout: float = 120.0):
    # A clean Windows environment can spend tens of seconds initializing the
    # first SciPy-backed pHash call. The application remains responsive and the
    # subsequent comparisons are fast, so the integration test waits for the
    # completed result instead of treating cold-start initialization as a
    # functional failure.
    deadline = time.monotonic() + timeout
    job = None
    while time.monotonic() < deadline:
        job = client.get(f"/api/status/{job_id}", headers=headers).get_json()
        if job.get("status") in {"complete", "error"}:
            return job
        time.sleep(0.1)
    return job


class ApplicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        application.app.config["TESTING"] = True
        cls.client = application.app.test_client()
        cls.request_headers = {"X-VeriReel-Request": "1"}

    def test_health_and_security_headers(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "ok"})
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])
        self.assertEqual(response.headers["X-DNS-Prefetch-Control"], "off")
        self.assertRegex(response.headers["X-Request-ID"], r"^[0-9a-f]{24}$")

    def test_untrusted_host_header_is_rejected(self):
        response = self.client.get("/api/health", headers={"Host": "attacker.example"})
        self.assertEqual(response.status_code, 400)

    def test_hsts_is_only_emitted_for_https_when_enabled(self):
        previous = application.app.config["ENABLE_HSTS"]
        application.app.config["ENABLE_HSTS"] = True
        try:
            insecure = self.client.get("/api/health")
            secure = self.client.get("/api/health", base_url="https://localhost")
        finally:
            application.app.config["ENABLE_HSTS"] = previous
        self.assertNotIn("Strict-Transport-Security", insecure.headers)
        self.assertEqual(secure.headers["Strict-Transport-Security"], "max-age=31536000")

    def test_forwarded_for_cannot_bypass_rate_limit_without_trusted_proxy(self):
        with application._jobs_lock:
            application._rate_windows.clear()
        previous_limit = application.RATE_LIMIT_REQUESTS
        application.RATE_LIMIT_REQUESTS = 2
        try:
            responses = [
                self.client.post(
                    "/api/analyze",
                    json={"url1": "invalid", "url2": "invalid"},
                    headers={
                        **self.request_headers,
                        "X-Forwarded-For": f"203.0.113.{index}",
                    },
                )
                for index in range(1, 4)
            ]
        finally:
            application.RATE_LIMIT_REQUESTS = previous_limit
            with application._jobs_lock:
                application._rate_windows.clear()
        self.assertEqual([response.status_code for response in responses], [400, 400, 429])

    def test_non_tiktok_json_ingestion_is_rejected(self):
        response = self.client.post(
            "/api/analyze",
            json={"url1": "https://example.test/one", "url2": "https://example.test/two"},
            headers=self.request_headers,
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("tiktok.com", response.get_json()["error"])

    def test_tiktok_link_analysis_uses_transient_downloads(self):
        media = video_bytes()

        def fake_download(url, output_dir, slot, **_kwargs):
            path = Path(output_dir) / f"{slot}.avi"
            path.write_bytes(media)
            number = 1 if slot == "video1" else 2
            return path, {
                "title": f"Mock TikTok {number}",
                "filename": path.name,
                "author": f"creator{number}",
                "platform": "TikTok",
                "upload_date": f"2026-01-0{number}",
                "timestamp": 1767225600.0 + number,
                "description": "Mocked test video.",
                "source_url": url,
                "source_type": "tiktok_url",
                "view_count": number * 1000,
                "like_count": number * 100,
            }

        with patch.object(application, "download_tiktok", side_effect=fake_download):
            response = self.client.post(
                "/api/analyze",
                json={
                    "url1": "https://www.tiktok.com/@one/video/111",
                    "url2": "https://vm.tiktok.com/second/",
                    "threshold": 75,
                },
                headers=self.request_headers,
            )
            self.assertEqual(response.status_code, 202)
            created = response.get_json()
            job_id = created["job_id"]
            job_headers = {"X-Job-Token": created["access_token"]}

            job = wait_for_job(self.client, job_id, job_headers)

        self.assertIsNotNone(job)
        self.assertEqual(job["status"], "complete", job.get("message"))
        self.assertEqual(job["source_mode"], "tiktok")
        self.assertEqual(job["result"]["video1"]["source_type"], "tiktok_url")
        self.assertEqual(job["result"]["video2"]["platform"], "TikTok")
        self.assertGreater(job["result"]["similarity"]["overall"], 95)
        self.assertFalse((application.TEMP_DIR / job_id).exists())

    def test_upload_analysis_and_pdf_report(self):
        media = video_bytes()
        response = self.client.post(
            "/api/analyze",
            data={
                "video1": (io.BytesIO(media), "reference.avi"),
                "video2": (io.BytesIO(media), "comparison.avi"),
                "label1": "Reference",
                "label2": "Comparison",
                "date1": "2026-01-01",
                "date2": "2026-01-03",
                "threshold": "75",
            },
            content_type="multipart/form-data",
            headers=self.request_headers,
        )
        self.assertEqual(response.status_code, 202)
        created = response.get_json()
        job_id = created["job_id"]
        job_headers = {"X-Job-Token": created["access_token"]}

        unauthorised = self.client.get(f"/api/status/{job_id}")
        self.assertEqual(unauthorised.status_code, 404)
        unauthorised_pdf = self.client.get(
            f"/api/report/{job_id}.pdf",
            headers={"X-Job-Token": "wrong-token"},
        )
        self.assertEqual(unauthorised_pdf.status_code, 404)

        job = wait_for_job(self.client, job_id, job_headers)

        self.assertIsNotNone(job)
        self.assertEqual(job["status"], "complete", job.get("message"))
        self.assertGreater(job["result"]["similarity"]["overall"], 95)
        self.assertEqual(job["result"]["report"]["original_video"], "video1")
        self.assertEqual(len(job["result"]["video1"]["sha256"]), 64)
        self.assertIn("media_deleted_at", job["result"]["security"])

        pdf = self.client.get(f"/api/report/{job_id}.pdf", headers=job_headers)
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf.mimetype, "application/pdf")
        self.assertTrue(pdf.data.startswith(b"%PDF"))
        self.assertGreater(len(pdf.data), 2500)

    def test_state_changes_require_custom_header(self):
        response = self.client.post(
            "/api/analyze",
            json={
                "url1": "https://www.tiktok.com/@one/video/111",
                "url2": "https://www.tiktok.com/@two/video/222",
            },
        )
        self.assertEqual(response.status_code, 403)

    def test_mismatched_video_signature_is_rejected(self):
        response = self.client.post(
            "/api/analyze",
            data={
                "video1": (io.BytesIO(b"not a video"), "reference.mp4"),
                "video2": (io.BytesIO(b"not a video"), "comparison.mp4"),
            },
            content_type="multipart/form-data",
            headers=self.request_headers,
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("recognised", response.get_json()["error"])


if __name__ == "__main__":
    unittest.main()
