from unittest.mock import patch

from django.db import DatabaseError
from django.test import TestCase
from django.urls import reverse


class HealthAndSecurityHeadersTests(TestCase):
    def test_liveness_does_not_require_database_details(self):
        response = self.client.get(reverse("health-live"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_readiness_reports_generic_database_failure(self):
        with patch(
            "sitecore.views.connection.cursor", side_effect=DatabaseError("private details")
        ):
            response = self.client.get(reverse("health-ready"))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "unavailable"})
        self.assertNotContains(response, "private details", status_code=503)

    def test_public_pages_receive_security_headers(self):
        response = self.client.get("/")

        self.assertIn("default-src 'self'", response["Content-Security-Policy"])
        self.assertEqual(response["X-Frame-Options"], "DENY")
        self.assertEqual(response["Referrer-Policy"], "strict-origin-when-cross-origin")
