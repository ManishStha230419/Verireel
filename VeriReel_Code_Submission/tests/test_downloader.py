import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from utils.downloader import (
    _is_tiktok_cdn_url,
    _parse_embed_video_data,
    _require_successful_tiktok_response,
    _request_with_safe_redirects,
    download_tiktok,
    validate_tiktok_url,
)


class TikTokUrlValidationTests(unittest.TestCase):
    def test_accepts_tiktok_post_and_short_links(self):
        self.assertEqual(
            validate_tiktok_url("https://www.tiktok.com/@creator/video/123?share=1#share"),
            "https://www.tiktok.com/@creator/video/123",
        )
        self.assertEqual(
            validate_tiktok_url("https://vm.tiktok.com/ABC123/"),
            "https://vm.tiktok.com/ABC123/",
        )

    def test_rejects_non_tiktok_and_unsafe_urls(self):
        invalid = [
            "http://www.tiktok.com/@creator/video/123",
            "https://tiktok.com.evil.example/video/123",
            "https://example.com/tiktok/video/123",
            "https://tiktok.com/",
            "",
        ]
        for url in invalid:
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    validate_tiktok_url(url)

    def test_parses_official_embed_data_and_restricts_media_hosts(self):
        video_id = "123456789"
        payload = {
            "source": {
                "data": {
                    f"/embed/v2/{video_id}": {
                        "videoData": {"itemInfos": {"id": video_id}}
                    }
                }
            }
        }
        document = (
            '<script id="__FRONTITY_CONNECT_STATE__" type="application/json">'
            + json.dumps(payload)
            + "</script>"
        )
        self.assertEqual(_parse_embed_video_data(document, video_id)["itemInfos"]["id"], video_id)
        self.assertTrue(_is_tiktok_cdn_url("https://v19.tiktokcdn.com/video/path"))
        self.assertFalse(_is_tiktok_cdn_url("https://tiktokcdn.com.evil.example/video/path"))

    def test_parses_universal_rehydration_data(self):
        video_id = "987654321"
        payload = {
            "__DEFAULT_SCOPE__": {
                "webapp.video-detail": {
                    "itemInfo": {
                        "itemStruct": {
                            "id": video_id,
                            "desc": "Example",
                            "video": {
                                "duration": 12,
                                "playAddr": "https://v19.tiktokcdn.com/video/path",
                            },
                            "author": {"uniqueId": "creator"},
                            "stats": {"playCount": 10, "diggCount": 2},
                        }
                    }
                }
            }
        }
        document = (
            '<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">'
            + json.dumps(payload)
            + "</script>"
        )
        parsed = _parse_embed_video_data(document, video_id)
        self.assertEqual(parsed["itemInfos"]["id"], video_id)
        self.assertEqual(parsed["itemInfos"]["video"]["videoMeta"]["duration"], 12)

    def test_redirect_target_is_revalidated(self):
        redirect = Mock(status_code=302, headers={"location": "http://127.0.0.1/internal"})
        requests_module = Mock()
        requests_module.get.return_value = redirect
        with patch("utils.downloader._assert_public_hostname"):
            with self.assertRaises(RuntimeError):
                _request_with_safe_redirects(
                    requests_module,
                    "https://www.tiktok.com/embed/v2/123",
                    validator=lambda value: str(value).startswith("https://www.tiktok.com/"),
                    timeout=5,
                )
        self.assertEqual(requests_module.get.call_count, 1)

    def test_transient_tiktok_failures_are_retried_and_safely_reported(self):
        unavailable = Mock(status_code=503, headers={})
        requests_module = Mock()
        requests_module.get.return_value = unavailable

        with (
            patch("utils.downloader._assert_public_hostname"),
            patch("utils.downloader.time.sleep"),
        ):
            response = _request_with_safe_redirects(
                requests_module,
                "https://www.tiktok.com/embed/v2/123",
                validator=lambda value: str(value).startswith("https://www.tiktok.com/"),
                timeout=5,
            )

        self.assertIs(response, unavailable)
        self.assertEqual(requests_module.get.call_count, 4)
        with self.assertRaisesRegex(RuntimeError, "use the upload option"):
            _require_successful_tiktok_response(response)

    def test_handled_primary_extractor_error_uses_silent_logger_and_fallback(self):
        from yt_dlp.utils import DownloadError

        fallback_result = (Path("fallback.mp4"), {"platform": "TikTok"})
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch("yt_dlp.YoutubeDL") as youtube_dl,
                patch(
                    "utils.downloader._download_from_official_embed",
                    return_value=fallback_result,
                ) as fallback,
            ):
                youtube_dl.return_value.__enter__.return_value.extract_info.side_effect = (
                    DownloadError("expected primary extractor failure")
                )
                result = download_tiktok(
                    "https://www.tiktok.com/@creator/video/123",
                    Path(directory),
                    "video1",
                )

        options = youtube_dl.call_args.args[0]
        logger = options["logger"]
        self.assertTrue(options["quiet"])
        self.assertTrue(options["no_warnings"])
        self.assertIsNone(logger.debug("debug"))
        self.assertIsNone(logger.warning("warning"))
        self.assertIsNone(logger.error("error"))
        fallback.assert_called_once()
        self.assertEqual(result, fallback_result)


if __name__ == "__main__":
    unittest.main()
