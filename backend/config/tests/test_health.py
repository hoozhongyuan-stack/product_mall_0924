import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase, override_settings


class HealthTests(TestCase):
    def test_ready_requires_database_and_writable_media_directory(self):
        with tempfile.TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=Path(directory)):
            response = self.client.get("/healthz")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"ready": True})
            self.assertEqual(response["Cache-Control"], "no-store")
        with override_settings(MEDIA_ROOT=Path(directory)):
            response = self.client.get("/healthz")
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json(), {"ready": False})

    def test_database_error_and_non_get_are_not_ready(self):
        with patch("config.health.connection.cursor", side_effect=OSError("synthetic database failure")):
            response = self.client.get("/healthz")
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json(), {"ready": False})
        self.assertEqual(self.client.post("/healthz").status_code, 405)

    def test_media_write_failure_is_not_ready(self):
        with patch("config.health.tempfile.TemporaryFile", side_effect=OSError("synthetic disk failure")):
            response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"ready": False})
