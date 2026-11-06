"""HTTP regression for Python floats becoming integer JSON numbers in browsers."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from pdf_engine import export_pdf
from server import _fields_fingerprint
import test_web


def browser_numbers(value):
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {key: browser_numbers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [browser_numbers(item) for item in value]
    return value


def legacy_digest(fields):
    return sha256(json.dumps(fields, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":"), allow_nan=False).encode()).hexdigest()


class PreparedFingerprintTests(unittest.TestCase):
    def setUp(self):
        self.web = test_web.HostedWebTest()
        self.web.setUp()
        self.addCleanup(self.web.tearDown)
        self.meta = self.web.job()
        self.job = self.meta["documentId"]
        self.assertEqual(self.web.prepare(self.meta).status_code, 200)
        self.web.verify(self.job)
        checkout = self.web.call("POST", f"/api/jobs/{self.job}/checkout", json={})
        self.assertEqual(checkout.status_code, 200, checkout.json)
        self.directory = Path(self.web.temp.name) / "jobs" / self.job
        self.original_pdf = (self.directory / "final.pdf").read_bytes()

    def test_browser_numeric_round_trip_reuses_prepared_pdf_while_payment_pending(self):
        fields = browser_numbers(self.meta["fields"])
        self.assertEqual(fields, self.meta["fields"])
        self.assertNotEqual(legacy_digest(fields), legacy_digest(self.meta["fields"]))
        with patch("server.export_pdf", wraps=export_pdf) as exporter:
            response = self.web.prepare(self.meta, fields)
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json["state"], "payment_pending")
        exporter.assert_not_called()
        self.assertEqual((self.directory / "final.pdf").read_bytes(), self.original_pdf)
        self.assertFalse(response.json["quotaConsumed"])

    def test_legacy_fingerprint_migrates_without_reexport_or_false_correction(self):
        path = self.directory / "metadata.json"
        metadata = json.loads(path.read_text(encoding="utf-8"))
        metadata["preparedFieldsDigest"] = legacy_digest(metadata["fields"])
        metadata["initialFieldsDigest"] = legacy_digest(metadata["fields"])
        path.write_text(json.dumps(metadata), encoding="utf-8")
        with patch("server.export_pdf", wraps=export_pdf) as exporter:
            response = self.web.prepare(self.meta, browser_numbers(self.meta["fields"]))
            repeated = self.web.prepare(self.meta, browser_numbers(self.meta["fields"]))
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(repeated.status_code, 200, repeated.json)
        exporter.assert_not_called()
        self.assertEqual((self.directory / "final.pdf").read_bytes(), self.original_pdf)
        self.assertFalse(json.loads(path.read_text(encoding="utf-8"))["correctionMarked"])

    def test_real_semantic_change_is_still_blocked_during_payment(self):
        for key, value in (("x", self.meta["fields"][0]["x"] + 1), ("name", "Changed field")):
            with self.subTest(key=key):
                fields = deepcopy(self.meta["fields"])
                fields[0][key] = value
                response = self.web.prepare(self.meta, fields)
                self.assertEqual(response.status_code, 409, response.json)
                self.assertEqual(response.json.get("code"), "payment_pending")
                self.assertEqual((self.directory / "final.pdf").read_bytes(), self.original_pdf)

    def test_canonical_fingerprint_keeps_all_semantics_without_mutating_fields(self):
        fields = deepcopy(self.meta["fields"])
        fields[0]["checked"] = True
        original = deepcopy(fields)
        fingerprint = _fields_fingerprint(fields)
        self.assertEqual(fingerprint, _fields_fingerprint(browser_numbers(fields)))
        self.assertEqual(fields, original)
        changes = {"name": "Different", "type": "checkbox", "page": 1, "id": "different-id",
                   "source": "manual", "confidence": .1, "x": fields[0]["x"] + 1,
                   "y": fields[0]["y"] + 1, "width": fields[0]["width"] + 1,
                   "height": fields[0]["height"] + 1, "checked": 1, "extra": {"nested": "value"}}
        for key, value in changes.items():
            changed = deepcopy(fields)
            changed[0][key] = value
            with self.subTest(key=key):
                self.assertNotEqual(fingerprint, _fields_fingerprint(changed))
        self.assertNotEqual(fingerprint, _fields_fingerprint(list(reversed(fields))))


if __name__ == "__main__":
    unittest.main()
