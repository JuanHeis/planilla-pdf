"""Sandbox email stays private; live checkouts never escape a test instance."""
import copy
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

import pymupdf

from commerce import Commerce, CommerceError, TEST_EMAIL_RETENTION
from server import create_app, demo_pdf
from test_web import Providers


class SandboxTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.directory.cleanup)
        self.env = patch.dict(os.environ, {
            "PUBLIC_BASE_URL": "http://127.0.0.1:8780", "PLANILLA_DATA_DIR": self.directory.name,
            "APP_ENV": "test", "PLANILLA_TEST_EMAIL": "1", "PLANILLA_BACKGROUND_CLEANUP": "0",
            "ADMIN_TOKEN": "offline-admin", "MERCADOPAGO_ACCESS_TOKEN": "offline",
            "MERCADOPAGO_WEBHOOK_SECRET": "offline-secret", "MERCADOPAGO_COLLECTOR_ID": "12345",
            # Even if real-mail settings exist, the sink must take priority.
            "RESEND_API_KEY": "offline-email", "EMAIL_FROM": "test@example.test",
            "SMTP_USERNAME": "", "SMTP_PASSWORD": "",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.now = time.time()
        self.providers = Providers()
        self.shop = Commerce(http_request=self.providers, clock=lambda: self.now)
        self.app = create_app(mode="web", commerce_service=self.shop)
        self.client = self.app.test_client()
        self.base = "http://127.0.0.1:8780"
        self.admin = {"Authorization": "Bearer offline-admin"}

    def call(self, method, path, client=None, **kwargs):
        response = (client or self.client).open(path, method=method, base_url=self.base, **kwargs)
        response.get_data()
        response.close()
        return response

    def job(self, pages=1):
        data = demo_pdf()
        if pages > 1:
            with pymupdf.open(stream=data, filetype="pdf") as original, pymupdf.open() as document:
                for _ in range(pages):
                    document.insert_pdf(original)
                data = document.tobytes()
        response = self.call("POST", "/api/detect", data=data, content_type="application/pdf")
        self.assertEqual(response.status_code, 200, response.json)
        meta = response.json
        response = self.call("POST", "/api/prepare", json={"documentId": meta["documentId"], "fields": meta["fields"]})
        self.assertEqual(response.json["state"], "needs_email")
        return meta["documentId"]

    def request_email(self, job, email="sandbox-customer@example.test"):
        response = self.call("POST", f"/api/jobs/{job}/email", json={"email": email})
        self.assertEqual(response.status_code, 200, response.json)
        return response

    def mailbox(self):
        response = self.call("GET", "/api/admin/test-mailbox", headers=self.admin)
        self.assertEqual(response.status_code, 200, response.json)
        return response.json

    def verify(self, job):
        self.request_email(job)
        link = self.mailbox()["messages"][0]["verificationUrl"]
        response = self.call("GET", link)
        self.assertEqual(response.status_code, 303)
        return link

    def test_mailbox_requires_admin_and_public_responses_do_not_contain_links(self):
        configuration = self.call("GET", "/api/config").json
        self.assertTrue(configuration["testMode"])
        self.assertTrue(configuration["testEmailEnabled"])
        self.assertTrue(configuration["emailConfigured"])
        job = self.job()
        response = self.request_email(job)
        self.assertEqual(response.json["state"], "email_pending")
        self.assertIn("bandeja de pruebas", response.json["message"])
        self.assertNotIn("/verify?", response.get_data(as_text=True))
        self.assertFalse(response.json["quotaConsumed"])
        for headers in ({}, {"Authorization": "Bearer wrong"}):
            response = self.call("GET", "/api/admin/test-mailbox", headers=headers)
            self.assertEqual(response.status_code, 401)
            self.assertNotIn("/verify?", response.get_data(as_text=True))
        inbox = self.mailbox()
        message = inbox["messages"][0]
        self.assertEqual(inbox["retentionSeconds"], 900)
        self.assertEqual(set(message), {"id", "to", "subject", "text", "verificationUrl", "created"})
        self.assertEqual(message["to"], "sandbox-customer@example.test")
        self.assertIn("Prueba", message["subject"])
        self.assertIn("no se envió un correo real", message["text"])
        self.assertEqual(urlsplit(message["verificationUrl"]).netloc, "127.0.0.1:8780")
        self.assertFalse(self.providers.mail)

    def test_verification_and_free_download_keep_normal_permissions(self):
        job = self.job()
        self.verify(job)
        status = self.call("GET", f"/api/jobs/{job}/status").json
        self.assertEqual(status["state"], "free_available")
        self.assertFalse(status["quotaConsumed"])
        stranger = self.app.test_client()
        self.assertEqual(self.call("GET", f"/api/jobs/{job}/download", client=stranger).status_code, 404)
        response = self.call("POST", f"/api/jobs/{job}/free", json={})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json["quotaConsumed"])
        downloaded = self.call("GET", f"/api/jobs/{job}/download")
        self.assertEqual(downloaded.status_code, 200)
        self.assertTrue(downloaded.data.startswith(b"%PDF-"))
        self.assertTrue(self.call("GET", f"/api/jobs/{job}/status").json["quotaConsumed"])
        self.assertFalse(self.providers.mail)

    def test_recovery_uses_private_sink_without_email_enumeration(self):
        job = self.job()
        self.verify(job)
        self.now += 61
        stranger = self.app.test_client()
        known = self.call("POST", "/api/recovery", client=stranger, json={"email": "sandbox-customer@example.test"})
        unknown = self.call("POST", "/api/recovery", client=stranger, json={"email": "nobody@example.test"})
        self.assertEqual(known.json, unknown.json)
        self.assertNotIn("/verify?", known.get_data(as_text=True))
        inbox = self.mailbox()
        self.assertEqual(len(inbox["messages"]), 2)
        response = self.call("GET", inbox["messages"][0]["verificationUrl"], client=stranger)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(self.call("GET", f"/api/jobs/{job}/status", client=stranger).status_code, 200)

    def test_expired_mail_is_removed_and_resend_still_requires_normal_token(self):
        job = self.job()
        self.request_email(job)
        previous = self.mailbox()["messages"][0]
        self.assertEqual(self.call("POST", f"/api/jobs/{job}/email", json={"email": previous["to"]}).status_code, 429)
        self.now += TEST_EMAIL_RETENTION + 1
        self.assertEqual(self.mailbox()["messages"], [])
        response = self.call("GET", previous["verificationUrl"])
        self.assertIn("verify_error=expired", response.location)
        self.request_email(job)
        current = self.mailbox()["messages"][0]
        self.assertNotEqual(previous["verificationUrl"], current["verificationUrl"])
        self.assertFalse(self.call("GET", f"/api/jobs/{job}/status").json["quotaConsumed"])

    def test_mailbox_retention_and_maximum_are_enforced_without_delivery(self):
        with self.shop._db() as db:
            for _ in range(105):
                self.shop._mail("fixture@example.test", "a" * 43, db)
        self.assertEqual(len(self.shop.test_mailbox()["messages"]), 100)
        self.now += TEST_EMAIL_RETENTION + 1
        self.shop.cleanup()
        with self.shop._db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM test_mail").fetchone()[0], 0)
        self.assertFalse(self.providers.mail)

    def test_disabled_sink_is_not_available_and_plaintext_is_removed(self):
        job = self.job()
        self.request_email(job)
        with patch.dict(os.environ, {"PLANILLA_TEST_EMAIL": "0", "APP_ENV": "production",
                                     "PUBLIC_BASE_URL": "https://planilla.example"}):
            disabled_shop = Commerce(http_request=self.providers)
            disabled = create_app(mode="web", commerce_service=disabled_shop).test_client()
            with disabled.get("/api/config", base_url="https://planilla.example") as response:
                self.assertFalse(response.json["testMode"])
                self.assertFalse(response.json["testEmailEnabled"])
            with disabled.get("/api/admin/test-mailbox", base_url="https://planilla.example", headers=self.admin) as response:
                self.assertEqual(response.status_code, 404)
            with disabled_shop._db() as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM test_mail").fetchone()[0], 0)

    def test_sink_requires_test_environment_and_private_admin_configuration(self):
        for environment in ("production", "development", ""):
            with patch.dict(os.environ, {"APP_ENV": environment}):
                with self.assertRaises(RuntimeError):
                    Commerce(self.directory.name)
                with self.assertRaises(RuntimeError):
                    create_app(mode="web", commerce_service=self.shop)
        with patch.dict(os.environ, {"ADMIN_TOKEN": ""}):
            with self.assertRaises(RuntimeError):
                create_app(mode="web", commerce_service=self.shop)
        with patch.dict(os.environ, {"PLANILLA_TEST_EMAIL": "true"}):
            with self.assertRaises(RuntimeError):
                create_app(mode="web", commerce_service=self.shop)

    def test_test_mail_service_cannot_be_injected_into_production(self):
        with patch.dict(os.environ, {"APP_ENV": "production", "PLANILLA_TEST_EMAIL": "0",
                                     "PUBLIC_BASE_URL": "https://planilla.example"}):
            with self.assertRaises(RuntimeError):
                create_app(mode="web", commerce_service=self.shop)

    def test_live_checkout_never_reaches_test_browser_or_cached_status(self):
        job = self.job(pages=3)
        self.verify(job)
        original = self.shop.http

        def live_provider(method, url, headers, payload=None):
            value = original(method, url, headers, payload)
            if method == "POST":
                value.update(id="ORDLIVE12345678", live_mode=True,
                             checkout_url="https://www.mercadopago.com.ar/checkout/v1/redirect?order_id=ORDLIVE12345678")
            return value

        self.shop.http = live_provider
        response = self.call("POST", f"/api/jobs/{job}/checkout", json={})
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json["code"], "payment_environment_mismatch")
        self.assertNotIn("checkoutUrl", response.json)
        with self.shop._db() as db:
            # A previous production deployment must not leak its cached live link.
            db.execute("UPDATE orders SET provider_id='ORDLIVE12345678',checkout_url=?,state='created'",
                       ("https://www.mercadopago.com.ar/checkout/v1/redirect?order_id=ORDLIVE12345678",))
        self.assertIsNone(self.shop.status(self.client.get_cookie("planilla_session", domain="127.0.0.1").value, job)["checkoutUrl"])
        self.assertEqual(self.call("POST", f"/api/jobs/{job}/checkout", json={}).status_code, 502)

    def test_test_checkout_never_reaches_production_browser(self):
        job = self.job(pages=3)
        self.verify(job)
        self.shop.production = True
        response = self.call("POST", f"/api/jobs/{job}/checkout", json={})
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json["code"], "payment_environment_mismatch")
        self.assertNotIn("checkoutUrl", response.json)

    def test_test_checkout_with_live_flag_is_rejected_before_redirect(self):
        job = self.job(pages=3)
        self.verify(job)
        original = self.shop.http

        def contradictory_provider(method, url, headers, payload=None):
            result = copy.deepcopy(original(method, url, headers, payload))
            if method == "POST":
                result["live_mode"] = True
            return result

        self.shop.http = contradictory_provider
        response = self.call("POST", f"/api/jobs/{job}/checkout", json={})
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("checkoutUrl", response.json)

    def test_live_checkout_is_valid_in_production_mode(self):
        job = self.job(pages=3)
        self.verify(job)
        original = self.shop.http

        def live_provider(method, url, headers, payload=None):
            value = original(method, url, headers, payload)
            if method == "POST":
                value.update(id="ORDLIVE12345678", live_mode=True,
                             checkout_url="https://www.mercadopago.com.ar/checkout/v1/redirect?order_id=ORDLIVE12345678")
            return value

        self.shop.production = True
        self.shop.http = live_provider
        response = self.call("POST", f"/api/jobs/{job}/checkout", json={})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertTrue(response.json["checkoutUrl"].endswith("order_id=ORDLIVE12345678"))

    def test_checkout_url_must_reference_the_validated_order(self):
        job = self.job(pages=3)
        self.verify(job)
        original = self.shop.http

        def wrong_link_provider(method, url, headers, payload=None):
            value = original(method, url, headers, payload)
            if method == "POST":
                value["checkout_url"] = "https://www.mercadopago.com.ar/checkout/v1/redirect?order_id=ORDLIVE12345678"
            return value

        self.shop.http = wrong_link_provider
        response = self.call("POST", f"/api/jobs/{job}/checkout", json={})
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json["code"], "invalid_checkout")
        self.assertNotIn("checkoutUrl", response.json)


if __name__ == "__main__":
    unittest.main()
