from datetime import datetime, timezone
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from usage_meter import DAY, UsageMeter, UsageSample


class UsageMeterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.now = datetime(2026, 11, 6, tzinfo=timezone.utc).timestamp()
        self.wall, self.cpu = 100.0, 3.0
        self.meter = UsageMeter(self.temp.name, clock=lambda: self.now,
                                wall_clock=lambda: self.wall, cpu_clock=lambda: self.cpu)

    def test_success_failure_and_retries_measure_work_without_double_counting_documents(self):
        with self.meter.measure("detect", session="session-test123", input_bytes=200) as sample:
            sample.job, sample.pages, sample.fields = "document-test123", 2, 18
            self.wall += 3
            self.cpu += 1.5
        for operation in ("render", "prepare", "download", "download"):
            with self.meter.measure(operation, job="document-test123", pages=2) as sample:
                self.wall += 1
                self.cpu += 0.25
                sample.output_bytes = 500
        with self.assertRaisesRegex(ValueError, "document content must not be persisted"):
            with self.meter.measure("detect", input_bytes=90):
                self.wall += 2
                self.cpu += 0.5
                raise ValueError("document content must not be persisted")
        report = self.meter.report("2026-11")
        self.assertEqual(report["totals"]["documents"], 1)
        self.assertEqual(report["totals"]["pages"], 2)
        self.assertEqual(report["totals"]["operations"], 6)
        self.assertEqual(report["totals"]["analysisAttempts"], 2)
        self.assertEqual(report["totals"]["failedAnalyses"], 1)
        self.assertEqual(report["totals"]["cpuSeconds"], 3)
        self.assertEqual(report["totals"]["elapsedSeconds"], 9)
        self.assertEqual(report["totals"]["outputBytes"], 2000)
        self.assertEqual(report["costs"]["actualMonthlyUSD"], None)
        self.assertEqual(report["costs"]["fullyAllocatedPerPdfUSD"], None)
        self.assertAlmostEqual(report["costs"]["cpuEstimateUSD"], 3 * .000463 / 60, places=8)
        with closing(sqlite3.connect(self.meter.database)) as db:
            data = str(db.execute("SELECT * FROM usage_records").fetchall())
        self.assertNotIn("document content", data)
        self.assertNotIn("document-test123", data)
        self.assertNotIn("session-test123", data)

    def test_monthly_cost_is_owner_entered_and_not_hobby_plus_usage(self):
        for job in ("document-111", "document-222"):
            self.meter.record(UsageSample("detect", job=job, pages=1, cpu_seconds=10))
        report = self.meter.set_monthly_actual("2026-11", "5.00")
        self.assertEqual(report["costs"]["actualMonthlyUSD"], 5)
        self.assertEqual(report["costs"]["fullyAllocatedPerPdfUSD"], 2.5)
        self.assertEqual(report["costs"]["hobbyBaselinePerPdfUSD"], 2.5)
        restarted = UsageMeter(self.temp.name, clock=lambda: self.now)
        self.assertEqual(restarted.report("2026-11")["costs"]["actualMonthlyUSD"], 5)
        self.assertEqual(self.meter.set_monthly_actual("2026-11", 7)["costs"]["fullyAllocatedPerPdfUSD"], 3.5)
        self.assertEqual(self.meter.set_monthly_actual("2026-11", 0)["costs"]["fullyAllocatedPerPdfUSD"], 0)

    def test_calendar_periods_and_empty_month_are_explicit(self):
        self.meter.record(UsageSample("detect", job="november-document", pages=3))
        self.now = datetime(2026, 12, 1, tzinfo=timezone.utc).timestamp()
        self.meter.record(UsageSample("render", job="november-document", pages=3))
        self.assertEqual(self.meter.report("2026-11")["totals"]["analysisAttempts"], 1)
        self.assertEqual(self.meter.report("2026-12")["totals"]["analysisAttempts"], 0)
        empty = self.meter.set_monthly_actual("2026-10", 5)
        self.assertEqual(empty["totals"]["documents"], 0)
        self.assertIsNone(empty["costs"]["fullyAllocatedPerPdfUSD"])
        self.assertEqual(empty["periodTimezone"], "UTC")

    def test_input_validation_does_not_accept_content_identifiers_or_nonfinite_money(self):
        for amount in (-1, True, "NaN", "Infinity", None, "bad", 1000001):
            with self.assertRaises(ValueError):
                self.meter.set_monthly_actual("2026-11", amount)
        for month in ("2026-13", "2026-1", "0000-01", "2026-11;DROP TABLE"):
            with self.assertRaises(ValueError):
                self.meter.report(month)
        with self.assertRaises(ValueError):
            self.meter.record(UsageSample("detect", session="person@example.com"))
        with self.assertRaises(ValueError):
            self.meter.record(UsageSample("detect", cpu_seconds=float("nan")))
        with self.assertRaises(ValueError):
            self.meter.record(UsageSample("detect", input_bytes=-1))

    def test_telemetry_failure_never_hides_business_exception(self):
        def unavailable(*args, **kwargs):
            raise sqlite3.OperationalError("disk full")
        self.meter.record = unavailable
        with self.assertLogs("usage_meter", "WARNING"):
            with self.meter.measure("render"):
                pass
        with self.assertLogs("usage_meter", "WARNING"):
            with self.assertRaisesRegex(RuntimeError, "actual failure"):
                with self.meter.measure("prepare"):
                    raise RuntimeError("actual failure")

    def test_cleanup_uses_separate_retention_for_operations_and_monthly_costs(self):
        self.meter.record(UsageSample("detect", job="expired-document", pages=1), created=self.now - 91 * DAY)
        self.meter.record(UsageSample("render", job="current-document", pages=2))
        self.meter.set_monthly_actual("2024-11", 5)
        self.meter.set_monthly_actual("2024-12", 6)
        self.meter.set_monthly_actual("2026-11", 7)
        self.meter.cleanup()
        with closing(sqlite3.connect(self.meter.database)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM usage_records").fetchone()[0], 1)
            self.assertEqual([r[0] for r in db.execute("SELECT month FROM monthly_actual ORDER BY month")],
                             ["2024-12", "2026-11"])

    def test_incomplete_old_month_does_not_divide_a_full_invoice_by_partial_detail(self):
        self.now = datetime(2026, 11, 6, tzinfo=timezone.utc).timestamp()
        created = datetime(2026, 8, 20, tzinfo=timezone.utc).timestamp()
        self.meter.record(UsageSample("detect", job="partially-retained", pages=2), created=created)
        report = self.meter.set_monthly_actual("2026-08", 5)
        self.assertTrue(report["retentionMayBePartial"])
        self.assertEqual(report["totals"]["documents"], 1)
        self.assertIsNone(report["costs"]["fullyAllocatedPerPdfUSD"])
        self.assertIsNone(report["costs"]["hobbyBaselinePerPdfUSD"])


if __name__ == "__main__":
    unittest.main()
