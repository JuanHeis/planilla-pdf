"""Real HTTP boundary with synthetic PDFs and injected (offline) providers."""
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import pymupdf

from commerce import Commerce, ORDER_API
from server import create_app, demo_pdf


class Providers:
    def __init__(self):
        self.mail = []
        self.orders = {}

    def __call__(self, method, url, headers, payload=None):
        if url == "https://api.resend.com/emails":
            self.mail.append(payload)
            return {"id": "offline-mail"}
        if url == ORDER_API and method == "POST":
            identifier = "ORDTST" + secrets.token_hex(13)
            value = {**payload, "id": identifier, "user_id": "12345", "currency": "ARS",
                     "total_paid_amount": "0.00", "status": "created", "status_detail": "created",
                     "last_updated_date": "2026-11-06T00:00:00Z",
                     "checkout_url": "https://www.mercadopago.com.ar/checkout/v1/redirect?order_id=" + identifier}
            self.orders[identifier] = value
            return value.copy()
        if url.startswith(ORDER_API + "/") and url.endswith("/refund") and method == "POST":
            identifier = url.split("/")[-2]
            self.orders[identifier].update(status="refunded", status_detail="refunded",
                                          last_updated_date="2026-11-06T02:00:00Z")
            return {"id": "offline-refund", "status": "processed"}
        if url.startswith(ORDER_API + "/"):
            identifier = url.split("/")[-1]
            return self.orders[identifier].copy()
        raise AssertionError("Unexpected external call: " + method + " " + url)

    def verification_url(self):
        return re.search(r"http[^\s]+/verify\?token=[A-Za-z0-9_-]+", self.mail[-1]["text"])[0]


class HostedWebTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.env = patch.dict(os.environ, {
            "PUBLIC_BASE_URL": "http://127.0.0.1:8780", "PLANILLA_DATA_DIR": self.temp.name,
            "APP_ENV": "test", "RESEND_API_KEY": "offline", "EMAIL_FROM": "Planilla <test@example.test>",
            "MERCADOPAGO_ACCESS_TOKEN": "offline", "MERCADOPAGO_WEBHOOK_SECRET": "offline-secret",
            "MERCADOPAGO_COLLECTOR_ID": "12345", "ADMIN_TOKEN": "offline-admin",
        })
        self.env.start()
        self.providers = Providers()
        self.shop = Commerce(http_request=self.providers)
        self.app = create_app(mode="web", commerce_service=self.shop)
        self.app.testing = True
        self.client = self.app.test_client()
        self.base = "http://127.0.0.1:8780"

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def call(self, method, path, **kwargs):
        response = self.client.open(path, method=method, base_url=self.base, **kwargs)
        response.get_data()
        response.close()
        return response

    def job(self, data=None):
        response = self.call("POST", "/api/detect", data=data or demo_pdf(), content_type="application/pdf")
        self.assertEqual(response.status_code, 200, response.json)
        meta = response.json
        self.assertTrue(meta["fields"])
        return meta

    def prepare(self, meta, fields=None):
        return self.call("POST", "/api/prepare", json={"documentId": meta["documentId"],
                         "fields": meta["fields"] if fields is None else fields})

    def verify(self, job, email="customer@example.test"):
        response = self.call("POST", f"/api/jobs/{job}/email", json={"email": email})
        self.assertEqual(response.status_code, 200, response.json)
        response = self.call("GET", self.providers.verification_url())
        self.assertEqual(response.status_code, 303)

    def test_free_consumed_at_download_only_and_no_other_session_access(self):
        meta = self.job()
        job = meta["documentId"]
        self.assertEqual(self.prepare(meta).json["state"], "needs_email")
        self.assertEqual(self.call("GET", f"/api/jobs/{job}/download").status_code, 403)
        self.verify(job)
        status = self.call("GET", f"/api/jobs/{job}/status").json
        self.assertEqual(status["state"], "free_available")
        self.assertFalse(status["quotaConsumed"])
        stranger = self.app.test_client()
        for path in (f"/api/page/{job}/0", f"/api/jobs/{job}/metadata", f"/api/jobs/{job}/download"):
            self.assertEqual(stranger.get(path, base_url=self.base).status_code, 404)
        self.assertFalse(self.call("POST", f"/api/jobs/{job}/free", json={}).json["quotaConsumed"])
        downloaded = self.call("GET", f"/api/jobs/{job}/download")
        self.assertEqual(downloaded.status_code, 200)
        with pymupdf.open(stream=downloaded.data, filetype="pdf") as pdf:
            self.assertEqual(sum(len(list(page.widgets() or [])) for page in pdf), len(meta["fields"]))
        self.assertTrue(self.call("GET", f"/api/jobs/{job}/status").json["quotaConsumed"])
        self.assertEqual(self.call("GET", f"/api/jobs/{job}/download").status_code, 200)
        # Same-work edits do not require another entitlement.
        self.assertEqual(self.prepare(meta).json["state"], "download_ready")
        second = self.job()
        self.assertEqual(self.prepare(second).json["state"], "payment_required")
        self.assertEqual(self.call("GET", f'/api/jobs/{second["documentId"]}/download').status_code, 403)

    def test_existing_original_never_consumes_quota(self):
        with pymupdf.open(stream=demo_pdf(), filetype="pdf") as pdf:
            widget = pymupdf.Widget()
            widget.field_name, widget.field_type = "already", pymupdf.PDF_WIDGET_TYPE_TEXT
            widget.rect = pymupdf.Rect(50, 162, 300, 185)
            pdf[0].add_widget(widget)
            meta = self.job(pdf.tobytes())
        job = meta["documentId"]
        self.assertTrue(meta["existingFields"])
        response = self.call("GET", f"/api/jobs/{job}/original")
        self.assertEqual(response.status_code, 200)
        status = self.prepare(meta, fields=[]).json
        self.assertEqual(status["grantKind"], "original")
        self.assertFalse(status["quotaConsumed"])
        self.assertEqual(self.call("GET", f"/api/jobs/{job}/download").status_code, 200)
        # Adding new fields still needs verification/free/payment.
        self.assertEqual(self.prepare(meta).json["state"], "needs_email")

    def test_paid_download_needs_signed_verified_order_and_admin_is_private(self):
        first = self.job()
        first_id = first["documentId"]
        self.prepare(first)
        self.verify(first_id)
        self.call("POST", f"/api/jobs/{first_id}/free", json={})
        self.call("GET", f"/api/jobs/{first_id}/download")
        meta = self.job()
        job = meta["documentId"]
        self.prepare(meta)
        checkout = self.call("POST", f"/api/jobs/{job}/checkout", json={})
        self.assertEqual(checkout.status_code, 200, checkout.json)
        identifier = checkout.json["orderId"]
        repeated = self.call("POST", f"/api/jobs/{job}/checkout", json={})
        self.assertEqual(repeated.json["orderId"], identifier)
        self.assertEqual(len(self.providers.orders), 1)
        self.assertEqual(self.prepare(meta).status_code, 200)
        self.assertEqual(self.call("GET", f"/api/jobs/{job}/download?status=approved").status_code, 403)
        order = self.providers.orders[identifier]
        order.update(status="processed", status_detail="accredited", total_paid_amount="2990.00",
                     last_updated_date="2026-11-06T01:00:00Z")
        self.assertNotEqual(self.call("GET", f"/api/jobs/{job}/status").json["state"], "download_ready")
        path = "/api/webhooks/mercadopago?data.id=" + identifier
        self.assertEqual(self.call("POST", path, json={}).status_code, 401)
        timestamp = str(int(time.time()))
        manifest = f"id:{identifier.lower()};request-id:offline-request;ts:{timestamp};"
        digest = hmac.new(b"offline-secret", manifest.encode(), "sha256").hexdigest()
        headers = {"x-request-id": "offline-request", "x-signature": f"ts={timestamp},v1={digest}"}
        self.assertEqual(self.call("POST", path, json={}, headers=headers).status_code, 200)
        self.assertEqual(self.call("POST", path, json={}, headers=headers).status_code, 200)
        self.assertEqual(self.call("GET", f"/api/jobs/{job}/download").status_code, 200)
        current_clock = self.shop.clock
        self.shop.clock = lambda: current_clock() + 25 * 3600
        self.assertEqual(self.prepare(meta).status_code, 200)
        changed = [{**meta["fields"][0], "name": "Cambió"}, *meta["fields"][1:]]
        self.assertEqual(self.prepare(meta, changed).status_code, 409)
        self.shop.clock = current_clock
        self.assertEqual(self.call("GET", "/api/admin/metrics").status_code, 401)
        metrics = self.call("GET", "/api/admin/metrics", headers={"Authorization": "Bearer offline-admin"})
        self.assertEqual(metrics.status_code, 200, metrics.json)
        self.assertEqual(metrics.json["counts"]["payment_approved"], 1)
        self.assertEqual(self.call("POST", f"/api/jobs/{job}/claim", json={"reason": "Faltaron varios campos en el resultado."}).status_code, 200)

    def test_bad_file_limits_csrf_and_local_learning_are_blocked(self):
        response = self.call("POST", "/api/detect", data=b"no pdf", content_type="application/pdf")
        self.assertEqual(response.status_code, 400)
        response = self.call("POST", "/api/detect", data=b"%PDF-1.7\ncorrupt", content_type="application/pdf")
        self.assertEqual(response.status_code, 400)
        with pymupdf.open() as pdf:
            for _ in range(6):
                pdf.new_page()
            response = self.call("POST", "/api/detect", data=pdf.tobytes(), content_type="application/pdf")
        self.assertEqual(response.status_code, 400)
        self.assertIn("No se consumió", response.json["error"])
        self.assertEqual(self.call("GET", "/static/ocr.html").status_code, 404)
        self.assertEqual(self.call("GET", "/api/ocr/learning").status_code, 404)
        self.assertEqual(self.call("POST", "/api/detect", headers={"Origin": "https://attacker.test"}).status_code, 403)
        self.assertEqual(self.client.get("/", base_url="http://attacker.test").status_code, 403)
        response = self.call("GET", "/verify?token=invalid")
        self.assertEqual(response.status_code, 303)
        self.assertIn("verify_error", response.location)

    def test_restart_preserves_files_and_grants(self):
        meta = self.job()
        job = meta["documentId"]
        self.prepare(meta)
        self.verify(job)
        self.call("POST", f"/api/jobs/{job}/free", json={})
        self.call("GET", f"/api/jobs/{job}/download")
        restarted_shop = Commerce(http_request=self.providers)
        restarted = create_app(mode="web", commerce_service=restarted_shop).test_client()
        restarted.set_cookie("planilla_session", self.client.get_cookie("planilla_session", domain="127.0.0.1").value,
                             domain="127.0.0.1")
        response = restarted.get(f"/api/jobs/{job}/status", base_url=self.base)
        self.assertEqual(response.json["state"], "download_ready")
        with restarted.get(f"/api/jobs/{job}/download", base_url=self.base) as response:
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.data.startswith(b"%PDF-"))

    def test_incomplete_payment_configuration_cannot_create_order(self):
        meta = self.job()
        job = meta["documentId"]
        self.prepare(meta)
        self.verify(job)
        self.shop.webhook_secret = ""
        response = self.call("POST", f"/api/jobs/{job}/checkout", json={})
        self.assertEqual(response.status_code, 503)
        self.assertFalse(self.providers.orders)
        with patch.dict(os.environ, {"APP_ENV": "production"}):
            with self.assertRaises(RuntimeError):
                create_app(mode="web", commerce_service=self.shop)

    def test_metrics_count_a_work_once_after_retries_and_note_corrections(self):
        meta = self.job()
        self.prepare(meta)
        self.prepare(meta)
        changed = [{**meta["fields"][0], "name": "Nombre corregido"}, *meta["fields"][1:]]
        self.prepare(meta, changed)
        self.prepare(meta, changed)
        counts = self.shop.metrics()["counts"]
        self.assertEqual(counts["prepared"], 1)
        self.assertEqual(counts["preview_ready"], 1)
        self.assertEqual(counts["corrected"], 1)


if __name__ == "__main__":
    unittest.main()
