"""Learning and cost boundaries through the real Flask routes, offline providers."""
from io import BytesIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

import pymupdf

from commerce import Commerce
from field_learning import FieldLearner
from server import create_app, demo_pdf
from test_web import Providers
from usage_meter import UsageMeter


class LearningWebTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.directory.cleanup)
        self.env = patch.dict(os.environ, {
            "PUBLIC_BASE_URL": "http://127.0.0.1:8780", "PLANILLA_DATA_DIR": self.directory.name,
            "APP_ENV": "test", "PLANILLA_BACKGROUND_CLEANUP": "0", "ADMIN_TOKEN": "offline-admin",
            "PLANILLA_TEST_EMAIL": "0",
            "RESEND_API_KEY": "offline", "EMAIL_FROM": "test@example.test",
            "SMTP_USERNAME": "", "SMTP_PASSWORD": "",
            "MERCADOPAGO_ACCESS_TOKEN": "offline", "MERCADOPAGO_WEBHOOK_SECRET": "offline-secret",
            "MERCADOPAGO_COLLECTOR_ID": "12345",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.providers = Providers()
        self.shop = Commerce(http_request=self.providers)
        self.learner = FieldLearner(self.directory.name)
        self.meter = UsageMeter(self.directory.name)
        self.app = create_app(mode="web", commerce_service=self.shop, usage_meter=self.meter, field_learner=self.learner)
        self.client = self.app.test_client()
        self.base = "http://127.0.0.1:8780"
        self.admin = {"Authorization": "Bearer offline-admin"}

    def call(self, method, path, client=None, **kwargs):
        response = (client or self.client).open(path, method=method, base_url=self.base, **kwargs)
        response.get_data()
        response.close()
        return response

    def detect(self, data=None):
        response = self.call("POST", "/api/detect", data=data or demo_pdf(), content_type="application/pdf")
        self.assertEqual(response.status_code, 200, response.json)
        return response.json

    def review(self, meta, **overrides):
        body = {"documentId": meta["documentId"], "fields": meta["fields"], "reviewed": True, "contribute": True}
        body.update(overrides)
        return self.call("POST", "/api/field-learning/review", json=body)

    def test_review_requires_two_explicit_confirmations_and_owner(self):
        meta = self.detect()
        for overrides in ({"reviewed": False}, {"contribute": False}, {"reviewed": "true"}, {"contribute": 1}):
            self.assertEqual(self.review(meta, **overrides).status_code, 400)
        foreign = self.app.test_client()
        response = self.call("POST", "/api/field-learning/review", client=foreign, json={
            "documentId": meta["documentId"], "fields": meta["fields"], "reviewed": True, "contribute": True,
        })
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.learner.stats()["reviews"], 0)

    def test_review_saves_preparation_without_spending_quota_and_counts_operations_once(self):
        meta = self.detect()
        job = meta["documentId"]
        self.assertEqual(self.call("GET", f"/api/page/{job}/0").status_code, 200)
        response = self.review(meta)
        self.assertEqual(response.status_code, 200, response.json)
        self.assertTrue(response.json["saved"])
        self.assertFalse(response.json["autoApply"])
        self.assertEqual(response.json["learning"]["reviews"], 1)
        status = self.call("GET", f"/api/jobs/{job}/status").json
        self.assertEqual(status["state"], "needs_email")
        self.assertFalse(status["quotaConsumed"])
        self.assertEqual(self.call("GET", f"/api/jobs/{job}/download").status_code, 403)
        report = self.call("GET", "/api/admin/usage", headers=self.admin).json
        self.assertEqual(report["totals"]["documents"], 1)
        operations = {item["operation"]: item for item in report["operations"]}
        self.assertEqual(operations["detect"]["requests"], 1)
        self.assertEqual(operations["render"]["requests"], 1)
        self.assertEqual(operations["prepare"]["requests"], 1)
        self.assertEqual(operations["download"]["failed"], 1)
        self.assertEqual(self.review(meta).status_code, 200)
        self.assertEqual(self.learner.stats()["reviews"], 1)

    def test_cost_and_learning_administration_cannot_be_used_anonymously(self):
        for path in ("/api/admin/usage", "/api/admin/field-learning", "/api/admin/field-learning/export"):
            self.assertEqual(self.call("GET", path).status_code, 401)
        for path, payload in (("/api/admin/usage/actual", {"month": "2026-11", "totalUSD": 1}),
                              ("/api/admin/field-learning/settings", {"autoApply": True})):
            self.assertEqual(self.call("POST", path, json=payload).status_code, 401)
        self.assertEqual(self.call("POST", "/api/admin/field-learning/import", data=b"zip", content_type="application/zip").status_code, 401)
        self.assertEqual(self.call("GET", "/api/field-learning/export").status_code, 404)
        self.assertEqual(self.call("POST", "/api/field-learning/import", data=b"zip", content_type="application/zip").status_code, 404)
        enable = self.call("POST", "/api/admin/field-learning/settings", json={"autoApply": True}, headers=self.admin)
        self.assertEqual(enable.status_code, 409)
        self.assertFalse(self.call("GET", "/api/field-learning").json["autoApply"])

    def test_export_contains_no_customer_names_email_job_or_pdf(self):
        meta = self.detect()
        fields = [{**f, "name": "DNI 12345678 customer@example.test"} for f in meta["fields"]]
        self.assertEqual(self.review(meta, fields=fields).status_code, 200)
        result = self.call("GET", "/api/admin/field-learning/export", headers=self.admin)
        self.assertEqual(result.status_code, 200)
        with ZipFile(BytesIO(result.data)) as archive:
            self.assertEqual(archive.namelist(), ["field-learning.json"])
            payload = archive.read("field-learning.json")
        for text in ("DNI", "12345678", "customer@example.test", meta["documentId"], "%PDF-"):
            self.assertNotIn(text.encode(), payload)
            self.assertNotIn(text.encode(), self.learner.database.read_bytes())
        self.assertEqual(self.call("POST", "/api/admin/field-learning/import", data=result.data,
                                   content_type="application/zip", headers=self.admin).status_code, 200)
        self.assertEqual(self.learner.stats()["reviews"], 1)

    def test_observation_is_default_until_owner_enables_eligible_rules(self):
        pages = [{"width": 600, "height": 800}]
        def field(x):
            return {"id": "detected-1", "page": 0, "x": x, "y": 100, "width": 200,
                    "height": 20, "type": "text", "source": "vector", "confidence": .87, "name": "Etiqueta"}
        for n in range(5):
            self.learner.record_review(f"training-{n}", [field(50 + n * 28)], [field(52 + n * 28)], pages)
        with pymupdf.open() as pdf:
            pdf.new_page(width=600, height=800)
            data = pdf.tobytes()
        detection = {"pages": pages, "fields": [field(250)], "existingFields": [], "warnings": []}
        with patch("server.detect_pdf", return_value=detection):
            first = self.detect(data)
            self.assertEqual(first["fields"][0]["x"], 250)
            self.assertEqual(first["learning"]["mode"], "shadow")
            self.assertFalse(first["learning"]["changes"][0]["applied"])
            enable = self.call("POST", "/api/admin/field-learning/settings", json={"autoApply": True}, headers=self.admin)
            self.assertEqual(enable.status_code, 200, enable.json)
            second = self.detect(data)
        self.assertEqual(second["fields"][0]["x"], 252)
        self.assertTrue(second["learning"]["changes"][0]["applied"])
        metadata = self.call("GET", f'/api/jobs/{second["documentId"]}/metadata').json
        self.assertEqual(metadata["baselineFields"][0]["x"], 250)
        self.assertEqual(self.review(second).status_code, 200)
        self.assertEqual(self.learner.stats()["correctedProposedFields"], 6)

    def test_broken_or_invalid_model_preserves_the_original_detector(self):
        with patch.object(self.learner, "suggest", side_effect=RuntimeError("offline learning")):
            original = self.detect()
        self.assertEqual(len(original["fields"]), 8)
        with patch.object(self.learner, "suggest", return_value={
            "fields": [{**original["fields"][0], "x": -500}], "mode": "conservative", "changes": [], "modelVersion": "invalid",
        }):
            unchanged = self.detect()
        self.assertEqual(unchanged["fields"], original["fields"])
        self.assertEqual(unchanged["learning"]["changes"], [])

    def test_review_respects_existing_editing_window(self):
        meta = self.detect()
        job = meta["documentId"]
        self.call("POST", "/api/prepare", json={"documentId": job, "fields": meta["fields"]})
        self.assertEqual(self.call("POST", f"/api/jobs/{job}/email", json={"email": "owner@example.test"}).status_code, 200)
        self.assertEqual(self.call("GET", self.providers.verification_url()).status_code, 303)
        self.call("POST", f"/api/jobs/{job}/free", json={})
        self.assertEqual(self.call("GET", f"/api/jobs/{job}/download").status_code, 200)
        original_clock = self.shop.clock
        self.shop.clock = lambda: original_clock() + 25 * 3600
        fields = [{**meta["fields"][0], "x": meta["fields"][0]["x"] + 1}, *meta["fields"][1:]]
        response = self.review(meta, fields=fields)
        self.assertEqual(response.status_code, 409, response.json)
        self.assertEqual(self.learner.stats()["reviews"], 0)

    def test_malformed_review_and_settings_fail_closed(self):
        meta = self.detect()
        self.assertEqual(self.review(meta, fields=None).status_code, 400)
        settings = Path(self.directory.name) / "field-learning-settings.json"
        settings.write_text("[]")
        response = self.call("GET", "/api/field-learning")
        self.assertEqual(response.status_code, 200, response.json)
        self.assertFalse(response.json["autoApply"])

    def test_unknown_job_requests_do_not_invent_processed_documents(self):
        self.assertEqual(self.call("GET", "/api/page/" + "1" * 32 + "/0").status_code, 404)
        report = self.call("GET", "/api/admin/usage", headers=self.admin).json
        self.assertEqual(report["totals"]["documents"], 0)
        self.assertEqual(report["totals"]["failedOperations"], 1)


if __name__ == "__main__":
    unittest.main()
