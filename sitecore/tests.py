from unittest.mock import patch
from uuid import UUID, uuid4

from django.core.exceptions import ValidationError
from django.db import DatabaseError
from django.test import TestCase
from django.urls import reverse

from accounts.models import User

from .models import AuditEvent


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

    def test_responses_receive_server_generated_correlation_ids(self):
        response = self.client.get(reverse("health-live"), HTTP_X_REQUEST_ID="attacker-id")

        request_id = UUID(response["X-Request-ID"])
        self.assertNotEqual(str(request_id), "attacker-id")


class AuditEventTests(TestCase):
    def test_audit_events_cannot_be_changed_or_deleted(self):
        user = User.objects.create_user("audit@example.com", "A-reasonably-long-password-82!")
        event = AuditEvent.objects.create(
            action=AuditEvent.Action.ACCOUNT_DATA_EXPORTED,
            actor=user,
            subject=user,
            request_id=uuid4(),
        )

        event.action = "changed"
        with self.assertRaises(ValidationError):
            event.save()
        with self.assertRaises(ValidationError):
            AuditEvent.objects.filter(pk=event.pk).update(action="changed")
        with self.assertRaises(ValidationError):
            event.delete()
