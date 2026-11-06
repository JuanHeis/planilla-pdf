from copy import deepcopy
from io import BytesIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile

from field_learning import FieldLearner


PAGES = [{"width": 600, "height": 800, "rotation": 0}]


def field(x=100, y=100, width=200, height=20, **extra):
    return {"id": "detected-1", "page": 0, "x": x, "y": y, "width": width, "height": height,
            "type": "text", "source": "vector", "confidence": .87, "name": "Nombre personal", **extra}


class FieldLearningTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.directory.cleanup)
        self.learner = FieldLearner(self.directory.name)

    def train_moves(self, count=5, dx=2, dy=1):
        for n in range(count):
            before = [field(x=50 + n * 28, y=100 + n * 25)]
            after = [{**before[0], "x": before[0]["x"] + dx, "y": before[0]["y"] + dy}]
            self.learner.record_review(f"private-job-{n}", before, after, PAGES)

    def test_held_out_layout_at_new_scale_gets_bounded_transfer(self):
        self.train_moves()
        original = [field(x=270, y=490, width=300, height=30, name="Otro formulario y versión")]
        held_out_pages = [{"width": 900, "height": 1200}]
        result = self.learner.suggest(original, held_out_pages)
        self.assertEqual(result["stats"]["layouts"], 5)
        self.assertEqual(result["fields"][0]["x"], 273)
        self.assertEqual(result["fields"][0]["y"], 491.5)
        self.assertEqual(result["fields"][0]["name"], "Otro formulario y versión")
        self.assertTrue(result["changes"][0]["applied"])
        self.assertEqual(original[0]["x"], 270)

    def test_one_form_reuploads_and_retries_are_not_independent_evidence(self):
        before, after = [field()], [field(x=102, y=101)]
        for n in range(20):
            self.learner.record_review("same-job", before, after, PAGES)
            self.learner.record_review(f"other-upload-{n}", before, after, PAGES, document_fingerprint=f"{n:064x}")
        result = self.learner.suggest([field(x=250, y=300)], PAGES)
        self.assertEqual(result["stats"]["reviews"], 21)
        self.assertEqual(result["stats"]["layouts"], 1)
        self.assertEqual(result["changes"], [])
        self.assertEqual(result["fields"][0]["x"], 250)

    def test_shadow_mode_and_empty_model_preserve_detector(self):
        original = [field()]
        self.assertEqual(self.learner.suggest(original, PAGES)["fields"], original)
        self.train_moves()
        result = self.learner.suggest(original, PAGES, auto_apply=False)
        self.assertEqual(result["fields"], original)
        self.assertEqual(result["mode"], "shadow")
        self.assertEqual(result["changes"][0]["geometry"]["x"], 102)
        self.assertFalse(result["changes"][0]["applied"])

    def test_negative_controls_do_not_transfer_to_different_structures(self):
        self.train_moves()
        controls = [field(width=70), field(source="raster"), field(type="checkbox", width=20),
                    field(x=2), field(y=600, height=80)]
        for candidate in controls:
            with self.subTest(candidate=candidate):
                self.assertEqual(self.learner.suggest([candidate], PAGES)["fields"], [candidate])
        # The same box now has a neighboring control: its layout context differs.
        row = [field(), field(x=320, id="detected-2")]
        self.assertEqual(self.learner.suggest(row, PAGES)["fields"], row)

    def test_contradiction_and_revised_review_disable_a_rule(self):
        self.train_moves()
        self.assertTrue(self.learner.suggest([field(x=260)], PAGES)["changes"])
        before = [field(x=50, y=100)]
        self.learner.record_review("private-job-0", before, before, PAGES)
        self.assertEqual(self.learner.suggest([field(x=260)], PAGES)["changes"], [])
        self.assertEqual(self.learner.stats()["reviews"], 5)
        self.learner.forget_review("private-job-0")
        self.assertEqual(self.learner.stats()["reviews"], 4)
        self.assertEqual(self.learner.suggest([field(x=260)], PAGES)["changes"], [])

    def test_disagreement_inside_one_coarse_pattern_blocks_generalization(self):
        for n in range(6):
            before = [field(x=60 + 20 * n)]
            after = [field(x=62 + 20 * n)]
            self.learner.record_review(f"changed-{n}", before, after, PAGES)
        before = [field(x=60)]
        self.learner.record_review("another-person-same-layout", before, before, PAGES)
        self.assertEqual(self.learner.suggest([field(x=260)], PAGES)["changes"], [])

    def test_only_low_confidence_false_positive_can_be_suppressed(self):
        for n in range(8):
            self.learner.record_review(f"deleted-{n}", [field(x=50 + n * 25, confidence=.59)], [], PAGES)
        low = self.learner.suggest([field(x=260, confidence=.59)], PAGES)
        self.assertEqual(low["fields"], [])
        self.assertEqual(low["changes"][0]["action"], "suppressed")
        high = [field(x=260, confidence=.87)]
        self.assertEqual(self.learner.suggest(high, PAGES)["fields"], high)

    def test_bounds_overlap_existing_and_manual_fields_are_protected(self):
        self.train_moves()
        original = [field(x=100, y=100)]
        existing = [field(x=300, y=100, width=20, id="existing-1", type="radio")]
        self.assertEqual(self.learner.suggest(original, PAGES, existing_fields=existing)["fields"], original)
        existing = [field(id="existing-1")]
        self.assertEqual(self.learner.suggest(existing, PAGES)["fields"], existing)
        manual = [field(id="manual-1", source="manual")]
        self.assertEqual(self.learner.suggest(manual, PAGES)["fields"], manual)
        # A normalized correction can be too large in points on an unusually large sheet.
        giant = [field(x=400, y=400, width=1200, height=120)]
        self.assertEqual(self.learner.suggest(giant, [{"width": 3600, "height": 4800}])["fields"], giant)

    def test_manual_additions_count_but_do_not_invent_fields(self):
        for n in range(8):
            self.learner.record_review(f"missed-{n}", [], [field(id="manual-1", source="manual")], PAGES)
        self.assertEqual(self.learner.stats()["addedFieldsReviewed"], 8)
        self.assertEqual(self.learner.suggest([], PAGES)["fields"], [])

    def test_id_fallback_uses_geometry_without_names(self):
        result = self.learner.record_review("test", [field(id="before")], [field(x=102, id="after", name="Private other text")], PAGES)
        self.assertEqual(result["changes"]["moved"], 1)
        self.assertEqual(result["changes"]["deleted"], 0)
        self.assertEqual(result["correctedProposedFields"], 1)

    def test_persistence_and_portable_learning_without_text_or_raw_ids(self):
        self.train_moves()
        original = [field(x=250, name="DNI 12345678 user@example.test")]
        self.learner.record_review("secret-job-reference", original, [field(x=252, y=101)], PAGES)
        reloaded = FieldLearner(self.directory.name)
        self.assertEqual(reloaded.suggest([field(x=300)], PAGES)["fields"][0]["x"], 302)
        bundle = reloaded.export_bundle()
        with ZipFile(BytesIO(bundle)) as archive:
            payload = archive.read("field-learning.json")
            self.assertEqual(archive.namelist(), ["field-learning.json"])
        for private in (b"DNI", b"12345678", b"user@example", b"secret-job-reference", b"private-job", b"Nombre"):
            self.assertNotIn(private, payload)
            self.assertNotIn(private, reloaded.database.read_bytes())
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as other:
            imported = FieldLearner(other)
            imported.import_bundle(bundle)
            first = imported.suggest([field(x=300)], PAGES)
            imported.import_bundle(bundle)
            self.assertEqual(imported.suggest([field(x=300)], PAGES), first)
            self.assertEqual(first["fields"][0]["x"], 302)
            # Round-tripping source snapshots cannot inflate our own evidence.
            self.learner.import_bundle(imported.export_bundle())
            self.assertEqual(self.learner.stats()["layouts"], 6)

    def test_import_cannot_turn_replicated_single_layout_into_five(self):
        before, after = [field()], [field(x=102)]
        for n in range(5):
            with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as other:
                small = FieldLearner(other)
                small.record_review(f"job-{n}", before, after, PAGES)
                self.learner.import_bundle(small.export_bundle())
        self.assertEqual(self.learner.stats()["eligibleAdjustments"], 0)

    def test_untrusted_import_and_invalid_review_are_rejected(self):
        with self.assertRaises(ValueError):
            self.learner.import_bundle(b"not a zip")
        stream = BytesIO()
        with ZipFile(stream, "w", ZIP_DEFLATED) as archive:
            archive.writestr("../field-learning.json", "{}")
        with self.assertRaises(ValueError):
            self.learner.import_bundle(stream.getvalue())
        self.train_moves()
        with ZipFile(BytesIO(self.learner.export_bundle())) as archive:
            payload = json.loads(archive.read("field-learning.json"))
        source = next(iter(payload["sources"].values()))
        source["personal data in a pattern"] = source.pop(next(iter(source)))
        stream = BytesIO()
        with ZipFile(stream, "w", ZIP_DEFLATED) as archive:
            archive.writestr("field-learning.json", json.dumps(payload))
        with self.assertRaises(ValueError):
            self.learner.import_bundle(stream.getvalue())
        with self.assertRaises(ValueError):
            self.learner.record_review("test", [field(x=-20)], [], PAGES)

    def test_import_rejects_inconsistent_aggregates_and_keeps_legitimate_roundtrip(self):
        from server import create_app

        self.train_moves()
        bundle = self.learner.export_bundle()
        before = self.learner.suggest([field(x=250)], PAGES)
        self.learner.import_bundle(bundle)
        self.assertEqual(self.learner.suggest([field(x=250)], PAGES), before)
        with ZipFile(BytesIO(bundle)) as archive:
            original = json.loads(archive.read("field-learning.json"))
        with patch.dict(os.environ, {"PUBLIC_BASE_URL": "http://127.0.0.1:8780", "APP_ENV": "test",
                                     "PLANILLA_DATA_DIR": self.directory.name, "PLANILLA_BACKGROUND_CLEANUP": "0",
                                     "ADMIN_TOKEN": "offline-admin"}):
            client = create_app(mode="web", field_learner=self.learner).test_client()
            for mode in ("mean_outside_range", "extremes_without_adjustments"):
                forged = deepcopy(original)
                patterns = next(iter(forged["sources"].values()))
                stats = patterns[next(iter(patterns))]
                if mode == "mean_outside_range":
                    stats["sum"][0] += .01
                else:
                    stats["approved"], stats["adjusted"] = stats["adjusted"], 0
                    stats["sum"] = [0.] * 4
                stream = BytesIO()
                with ZipFile(stream, "w", ZIP_DEFLATED) as archive:
                    archive.writestr("field-learning.json", json.dumps(forged))
                response = client.post("/api/admin/field-learning/import", data=stream.getvalue(),
                                       base_url="http://127.0.0.1:8780", content_type="application/zip",
                                       headers={"Authorization": "Bearer offline-admin"})
                self.assertEqual(response.status_code, 400, response.json)
                self.assertEqual(self.learner.suggest([field(x=250)], PAGES), before)

    def test_distinct_real_pdf_styles_transfer_without_seen_labels(self):
        import pymupdf
        from pdf_engine import detect_pdf

        def detect(x, width=600, height=800, font=10, color=(0, 0, 0)):
            scale = width / 600
            with pymupdf.open() as pdf:
                page = pdf.new_page(width=width, height=height)
                page.insert_text((x, 90 * scale), "Formulario distinto", fontsize=font, color=color)
                page.draw_rect(pymupdf.Rect(x, 100 * scale, x + 200 * scale, 124 * scale), color=color)
                return detect_pdf(pdf.tobytes())

        for n in range(5):
            data = detect(50 + 28 * n, font=9 + n, color=(n * .1, n * .1, n * .1))
            self.assertEqual(len(data["fields"]), 1)
            reviewed = [{**f, "x": f["x"] + 2, "y": f["y"] + 1} for f in data["fields"]]
            self.learner.record_review(f"real-{n}", data["fields"], reviewed, data["pages"])
        held_out = detect(240, width=900, height=1200, font=17, color=(0, .2, .5))
        result = self.learner.suggest(held_out["fields"], held_out["pages"])
        self.assertEqual(len(result["changes"]), 1)
        self.assertGreater(result["fields"][0]["x"], held_out["fields"][0]["x"])


if __name__ == "__main__":
    unittest.main()
