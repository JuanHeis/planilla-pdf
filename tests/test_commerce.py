import copy
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import hmac
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from commerce import Commerce, CommerceError, DAY, ORDER_API


class Provider:
    def __init__(self):
        self.calls, self.emails, self.orders = [], [], {}
        self.fail_create_once = False

    def __call__(self, method, url, headers, payload=None):
        self.calls.append((method, url, headers, copy.deepcopy(payload)))
        if url == "https://api.resend.com/emails":
            self.emails.append(payload)
            return {"id": uuid4().hex}
        if method == "POST" and url == ORDER_API:
            # Emulate provider idempotency even after an ambiguous client timeout.
            key = headers["X-Idempotency-Key"]
            existing = next((v for v in self.orders.values() if v["key"] == key), None)
            if existing is None:
                identifier = "ORDTST" + uuid4().hex
                existing = {"id": identifier, "key": key, "external_reference": payload["external_reference"],
                            "checkout_url": "https://www.mercadopago.com.ar/checkout/v1/redirect?order_id=" + identifier,
                            "status": "created", "status_detail": "created", "currency": "ARS", "user_id": 12345,
                            "total_amount": "2990.00", "total_paid_amount": "0.00",
                            "last_updated_date": "2026-11-06T10:00:00Z"}
                self.orders[identifier] = existing
            if self.fail_create_once:
                self.fail_create_once = False
                raise CommerceError("Provider timeout", 503, "provider_unavailable")
            return copy.deepcopy(existing)
        identifier = url.removeprefix(ORDER_API + "/").removesuffix("/refund")
        if method == "POST" and url.endswith("/refund"):
            self.orders[identifier].update(status="refunded", status_detail="refunded", last_updated_date="2026-11-06T12:00:00Z")
            return {"id": "refund-id", "status": "processed"}
        if method == "GET" and identifier in self.orders:
            return copy.deepcopy(self.orders[identifier])
        raise AssertionError((method, url))


class CommerceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.directory.cleanup)
        self.env = patch.dict(os.environ, {
            "PUBLIC_BASE_URL": "https://planilla.example", "APP_ENV": "test",
            "MERCADOPAGO_ACCESS_TOKEN": "test-token", "MERCADOPAGO_WEBHOOK_SECRET": "test-secret",
            "MERCADOPAGO_COLLECTOR_ID": "12345", "RESEND_API_KEY": "test-email-key",
            "EMAIL_FROM": "Planilla <test@example.com>", "SMTP_USERNAME": "", "SMTP_PASSWORD": "",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.now = 1793966400.0
        self.provider = Provider()
        self.shop = Commerce(self.directory.name, http_request=self.provider, clock=lambda: self.now)
        self.session = self.shop.ensure_session(None)

    def job(self, pages=1, existing=0, session=None):
        identifier = uuid4().hex
        self.shop.register_job(session or self.session, identifier, pages, existing)
        self.shop.prepare(session or self.session, identifier, b"%PDF-1.7 fixture")
        return identifier

    def verify(self, job, email="user@example.com", session=None):
        session = session or self.session
        self.shop.request_email(session, job, email)
        token = re.search(r"token=([A-Za-z0-9_-]+)", self.provider.emails[-1]["text"])[1]
        result = self.shop.verify(token)
        self.assertEqual(result, {"sessionId": session, "jobId": job})
        return token

    def webhook(self, identifier):
        request_id, timestamp = "request-123", str(int(self.now))
        signature = hmac.new(b"test-secret", f"id:{identifier.lower()};request-id:{request_id};ts:{timestamp};".encode(), sha256).hexdigest()
        return self.shop.process_webhook({"data.id": identifier}, {"x-request-id": request_id, "x-signature": f"ts={timestamp},v1={signature}"})

    def paid_order(self):
        job = self.job(pages=3)
        self.verify(job)
        checkout = self.shop.checkout(self.session, job)
        identifier = checkout["orderId"]
        self.provider.orders[identifier].update(status="processed", status_detail="accredited", total_paid_amount="2990.00", last_updated_date="2026-11-06T11:00:00Z")
        return job, identifier

    def test_original_download_keeps_free_and_extra_fields_require_entitlement(self):
        job = self.job(existing=2)
        state = self.shop.prepare(self.session, job, b"%PDF-1.7 original", original=True)
        self.assertEqual(state["state"], "download_ready")
        self.assertTrue(self.shop.download(self.session, job).is_file())
        self.assertFalse(self.shop.status(self.session, job)["quotaConsumed"])
        state = self.shop.prepare(self.session, job, b"%PDF-1.7 new fields")
        self.assertEqual(state["state"], "needs_email")
        with self.assertRaises(CommerceError):
            self.shop.download(self.session, job)

    def test_free_only_consumed_on_download_once_per_verified_email(self):
        job = self.job()
        self.verify(job)
        self.assertFalse(self.shop.status(self.session, job)["quotaConsumed"])
        self.shop.claim_free(self.session, job)
        self.assertFalse(self.shop.status(self.session, job)["quotaConsumed"])
        self.shop.download(self.session, job)
        self.shop.download(self.session, job)
        self.assertTrue(self.shop.status(self.session, job)["quotaConsumed"])
        self.assertEqual(self.shop.metrics()["counts"]["free_redeemed"], 1)
        self.now += 61
        second_session = self.shop.ensure_session(None)
        second = self.job(session=second_session)
        self.verify(second, session=second_session)
        with self.assertRaises(CommerceError) as error:
            self.shop.claim_free(second_session, second)
        self.assertEqual(error.exception.code, "free_unavailable")

    def test_ownership_unverified_and_page_limits(self):
        job = self.job()
        stranger = self.shop.ensure_session(None)
        for operation in (self.shop.authorize, self.shop.status, self.shop.download):
            with self.assertRaises(CommerceError) as error:
                operation(stranger, job)
            self.assertEqual(error.exception.status, 404)
        with self.assertRaises(CommerceError):
            self.shop.claim_free(self.session, job)
        long_job = uuid4().hex
        self.shop.register_job(self.session, long_job, 6)
        with self.assertRaises(CommerceError) as error:
            self.shop.prepare(self.session, long_job, b"%PDF-1.7 fixture")
        self.assertEqual(error.exception.code, "page_limit")

    def test_order_price_signature_and_authoritative_data_validation(self):
        job, identifier = self.paid_order()
        sent = next(call[3] for call in self.provider.calls if call[1] == ORDER_API)
        self.assertEqual(sent["total_amount"], "2990.00")
        self.assertIn("success_url", sent["config"]["online"])
        with self.assertRaises(CommerceError):
            self.shop.download(self.session, job)
        with self.assertRaises(CommerceError):
            self.shop.process_webhook({"data.id": identifier}, {"x-signature": "bad"})
        baseline = copy.deepcopy(self.provider.orders[identifier])
        for field, value in (("total_amount", "1.00"), ("currency", "USD"), ("user_id", 9),
                             ("external_reference", "wrong"), ("live_mode", True), ("total_paid_amount", "1.00")):
            self.provider.orders[identifier] = {**baseline, field: value}
            with self.subTest(field=field), self.assertRaises(CommerceError):
                self.webhook(identifier)
        self.provider.orders[identifier] = baseline
        self.webhook(identifier)
        self.webhook(identifier)
        self.assertTrue(self.shop.download(self.session, job).exists())
        self.assertEqual(self.shop.metrics()["counts"]["payment_approved"], 1)
        self.assertTrue(self.shop.status(self.session, job)["canClaim"])

    def test_browser_refresh_alone_cannot_unlock_and_webhook_can(self):
        job, identifier = self.paid_order()
        self.assertEqual(self.shop.refresh(self.session, job)["state"], "payment_pending")
        with self.assertRaises(CommerceError):
            self.shop.download(self.session, job)
        self.webhook(identifier)
        self.assertEqual(self.shop.status(self.session, job)["state"], "download_ready")

    def test_refund_tracks_the_order_that_granted_access_after_retry(self):
        job, first = self.paid_order()
        self.provider.orders[first].update(status="failed", status_detail="processing_error")
        self.webhook(first)
        second = self.shop.checkout(self.session, job)["orderId"]
        self.provider.orders[first].update(status="processed", status_detail="accredited", last_updated_date="2026-11-06T11:30:00Z")
        self.webhook(first)
        self.shop.claim(self.session, job, "Los campos salieron desalineados")
        claim = self.shop.claims()[0]
        self.assertEqual(claim["provider_id"], first)
        self.shop.refund(claim["id"])
        self.assertEqual(self.provider.orders[first]["status"], "refunded")
        self.assertEqual(self.provider.orders[second]["status"], "created")
        with self.assertRaises(CommerceError):
            self.shop.download(self.session, job)

    def test_provider_timeout_reuses_order_idempotency_and_reference(self):
        job = self.job(pages=3)
        self.verify(job)
        self.provider.fail_create_once = True
        with self.assertRaises(CommerceError):
            self.shop.checkout(self.session, job)
        state = self.shop.checkout(self.session, job)
        requests = [call for call in self.provider.calls if call[1] == ORDER_API]
        self.assertEqual(requests[0][2]["X-Idempotency-Key"], requests[1][2]["X-Idempotency-Key"])
        self.assertEqual(requests[0][3]["external_reference"], requests[1][3]["external_reference"])
        self.assertEqual(len(self.provider.orders), 1)
        self.assertEqual(self.shop.checkout(self.session, job)["orderId"], state["orderId"])

    def test_rejected_order_can_retry_and_reason_is_safe(self):
        job, identifier = self.paid_order()
        self.provider.orders[identifier].update(status="failed", status_detail="insufficient_amount")
        self.webhook(identifier)
        state = self.shop.status(self.session, job)
        self.assertEqual(state["state"], "payment_rejected")
        self.assertIn("saldo", state["message"])
        second = self.shop.checkout(self.session, job)
        self.assertNotEqual(identifier, second["orderId"])

    def test_claim_then_confirmed_refund_is_idempotent_and_revokes_access(self):
        job, identifier = self.paid_order()
        self.webhook(identifier)
        self.shop.claim(self.session, job, "Faltan varios campos importantes")
        self.shop.claim(self.session, job, "Lo solicito nuevamente por error")
        claims = self.shop.claims()
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["state"], "requested")
        self.assertFalse(any(call[1].endswith("/refund") for call in self.provider.calls))
        self.assertEqual(self.shop.refund(claims[0]["id"])["state"], "refunded")
        self.shop.refund(claims[0]["id"])
        self.assertEqual(sum(call[1].endswith("/refund") for call in self.provider.calls), 1)
        self.assertEqual(self.shop.status(self.session, job)["state"], "refunded")
        with self.assertRaises(CommerceError):
            self.shop.download(self.session, job)
        self.assertEqual(self.shop.metrics()["refundedARS"], 2990)

    def test_simultaneous_free_downloads_only_one_job_wins(self):
        first, second = self.job(), self.job()
        self.verify(first)
        self.shop.claim_free(self.session, first)
        self.shop.claim_free(self.session, second)

        def download(job):
            try:
                self.shop.download(self.session, job)
                return "downloaded"
            except CommerceError as error:
                return error.code

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(download, (first, second)))
        self.assertCountEqual(outcomes, ["downloaded", "free_unavailable"])
        self.assertEqual(self.shop.metrics()["counts"]["free_redeemed"], 1)

    def test_edit_window_expiry_preserves_last_pdf_download_and_audit(self):
        job = self.job()
        self.verify(job)
        self.shop.claim_free(self.session, job)
        self.shop.download(self.session, job)
        self.shop.prepare(self.session, job, b"%PDF-1.7 corrected")
        self.now += DAY + 1
        with self.assertRaises(CommerceError) as error:
            self.shop.prepare(self.session, job, b"%PDF-1.7 changed late")
        self.assertEqual(error.exception.code, "editing_expired")
        self.assertEqual(self.shop.download(self.session, job).read_bytes(), b"%PDF-1.7 corrected")
        self.now += 7 * DAY
        self.assertEqual(self.shop.status(self.session, job)["state"], "expired")
        self.shop.cleanup()
        self.assertFalse((self.shop.jobs_dir / job).exists())
        self.assertEqual(self.shop.metrics()["counts"]["free_redeemed"], 1)

    def test_email_validation_rate_limit_token_expiry_and_recovery(self):
        job = self.job()
        for invalid in (None, "user@example", "a\nb@example.com"):
            with self.assertRaises(CommerceError):
                self.shop.request_email(self.session, job, invalid)
        self.assertFalse(self.provider.emails)
        token = self.verify(job)
        with self.assertRaises(CommerceError) as error:
            self.shop.request_email(self.session, job, "user@example.com")
        self.assertEqual(error.exception.code, "email_rate_limit")
        self.now += 61
        generic = self.shop.recover("user@example.com")
        self.assertEqual(generic, self.shop.recover("unknown@example.com"))
        self.assertEqual(len(self.provider.emails), 2)
        self.assertEqual(self.shop.verify(token)["jobId"], job)
        self.now += 61
        new_job = self.job()
        self.shop.request_email(self.session, new_job, "other@example.com")
        expired = re.search(r"token=([A-Za-z0-9_-]+)", self.provider.emails[-1]["text"])[1]
        self.now += 901
        with self.assertRaises(CommerceError) as error:
            self.shop.verify(expired)
        self.assertEqual(error.exception.code, "expired_token")

    def test_email_not_configured_has_no_fake_success_and_smtp_fallback(self):
        job = self.job()
        with patch.dict(os.environ, {"RESEND_API_KEY": ""}):
            with self.assertRaises(CommerceError) as error:
                self.shop.request_email(self.session, job, "user@example.com")
            self.assertEqual(error.exception.code, "email_unconfigured")
            self.assertEqual(self.shop.status(self.session, job)["state"], "needs_email")
            with patch.dict(os.environ, {"SMTP_USERNAME": "sender@gmail.com", "SMTP_PASSWORD": "app-password"}), patch("commerce.smtplib.SMTP_SSL") as smtp:
                self.shop.request_email(self.session, job, "user@example.com")
                smtp.return_value.__enter__.return_value.login.assert_called_once_with("sender@gmail.com", "app-password")
                smtp.return_value.__enter__.return_value.send_message.assert_called_once()

    def test_partial_processed_and_stale_webhook_do_not_grant_or_revoke_incorrectly(self):
        job, identifier = self.paid_order()
        self.provider.orders[identifier].update(status_detail="partially_refunded")
        self.webhook(identifier)
        with self.assertRaises(CommerceError):
            self.shop.download(self.session, job)
        self.provider.orders[identifier].update(status_detail="accredited")
        self.webhook(identifier)
        self.provider.orders[identifier].update(status="failed", status_detail="rejected_by_issuer", last_updated_date="2026-11-06T10:01:00Z")
        self.webhook(identifier)
        self.assertEqual(self.shop.status(self.session, job)["state"], "download_ready")


if __name__ == "__main__":
    unittest.main()

