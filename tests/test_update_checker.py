import unittest
from unittest import mock

from app.modules.update_checker import check_for_update, compare_versions


class UpdateCheckerTests(unittest.TestCase):
    def test_compare_versions(self) -> None:
        self.assertEqual(0, compare_versions("2.0.0", "2.0.0"))
        self.assertEqual(1, compare_versions("2.0.1", "2.0.0"))
        self.assertEqual(-1, compare_versions("1.9.9", "2.0.0"))
        self.assertEqual(0, compare_versions("2.0", "2.0.0"))
        self.assertEqual(0, compare_versions("2.0.0-beta1", "2.0.0"))

    def test_check_for_update_reports_available(self) -> None:
        fake_response = mock.Mock()
        fake_response.raise_for_status.return_value = None
        fake_response.json.return_value = {
            "version": "2.1.0",
            "channel": "stable",
            "download_url": "https://example.com/DAVE-2.1.0.exe",
            "sha256": "abc123",
        }

        with mock.patch("app.modules.update_checker.requests.get", return_value=fake_response):
            result = check_for_update(
                current_version="2.0.0",
                manifest_url="https://example.com/manifest.json",
                timeout_seconds=2.0,
            )

        self.assertEqual("available", result["status"])
        self.assertTrue(result["available"])
        self.assertEqual("2.1.0", result["latest_version"])

    def test_check_for_update_handles_request_error(self) -> None:
        with mock.patch(
            "app.modules.update_checker.requests.get",
            side_effect=RuntimeError("network down"),
        ):
            result = check_for_update(
                current_version="2.0.0",
                manifest_url="https://example.com/manifest.json",
            )

        self.assertEqual("error", result["status"])
        self.assertFalse(result["available"])


if __name__ == "__main__":
    unittest.main()
