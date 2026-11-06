"""Operation measurements and owner-entered monthly infrastructure costs."""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from hashlib import sha256
import logging
import math
import os
from pathlib import Path
import re
import sqlite3
import time

DAY = 86400
OPERATIONS = {"detect", "render", "prepare", "download"}
CPU_RATE_PER_MINUTE = "0.000463"


class _Connection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


@dataclass
class UsageSample:
    operation: str
    job: str | None = None
    session: str | None = None
    input_bytes: int = 0
    output_bytes: int = 0
    pages: int = 0
    fields: int = 0
    success: bool = True
    cpu_seconds: float = 0
    elapsed_seconds: float = 0


def _month_bounds(month):
    if not isinstance(month, str) or not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise ValueError("El mes debe tener formato AAAA-MM.")
    year, number = map(int, month.split("-"))
    if not 2000 <= year <= 9998:
        raise ValueError("El año del período es inválido.")
    start = datetime(year, number, 1, tzinfo=timezone.utc)
    end = datetime(year + (number == 12), 1 if number == 12 else number + 1, 1, tzinfo=timezone.utc)
    return start, end


def _identifier_hash(value):
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", value):
        raise ValueError("El identificador de medición es inválido.")
    return sha256(value.encode()).hexdigest()


def _quantity(value, label):
    if type(value) is not int or not 0 <= value <= 2 ** 50:
        raise ValueError(f"La cantidad de {label} es inválida.")
    return value


def _seconds(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError("La duración medida es inválida.")
    return float(value)


class UsageMeter:
    def __init__(self, data_dir=None, *, clock=None, wall_clock=None, cpu_clock=None):
        directory = Path(data_dir or os.environ.get("PLANILLA_DATA_DIR", "data")).resolve()
        directory.mkdir(parents=True, exist_ok=True)
        self.database = directory / "usage.sqlite3"
        self.clock = clock or time.time
        self.wall_clock = wall_clock or time.perf_counter
        self.cpu_clock = cpu_clock or time.process_time
        try:
            self.cpu_rate = Decimal(os.environ.get("PLANILLA_CPU_USD_PER_MINUTE", CPU_RATE_PER_MINUTE))
        except InvalidOperation as error:
            raise ValueError("La tarifa estimada de CPU es inválida.") from error
        if not self.cpu_rate.is_finite() or self.cpu_rate < 0 or self.cpu_rate > 1000:
            raise ValueError("La tarifa estimada de CPU es inválida.")
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS usage_records (
                    id INTEGER PRIMARY KEY, created REAL NOT NULL, month TEXT NOT NULL,
                    operation TEXT NOT NULL, job_hash TEXT, session_hash TEXT,
                    success INTEGER NOT NULL, pages INTEGER NOT NULL, fields INTEGER NOT NULL,
                    input_bytes INTEGER NOT NULL, output_bytes INTEGER NOT NULL,
                    cpu_seconds REAL NOT NULL, elapsed_seconds REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS usage_by_month ON usage_records(month);
                CREATE INDEX IF NOT EXISTS usage_by_date ON usage_records(created);
                CREATE TABLE IF NOT EXISTS monthly_actual (
                    month TEXT PRIMARY KEY, total_cents INTEGER NOT NULL, updated REAL NOT NULL);
            """)

    def _db(self):
        db = sqlite3.connect(self.database, timeout=10, factory=_Connection)
        db.row_factory = sqlite3.Row
        return db

    @contextmanager
    def measure(self, operation, *, job=None, session=None, input_bytes=0, pages=0, fields=0):
        if operation not in OPERATIONS:
            raise ValueError("La operación de medición es inválida.")
        sample = UsageSample(operation, job, session, input_bytes=input_bytes, pages=pages, fields=fields)
        created, wall_start, cpu_start = self.clock(), self.wall_clock(), self.cpu_clock()
        try:
            yield sample
        except BaseException:
            sample.success = False
            raise
        finally:
            sample.cpu_seconds = max(0, self.cpu_clock() - cpu_start)
            sample.elapsed_seconds = max(0, self.wall_clock() - wall_start)
            try:
                self.record(sample, created=created)
            except (sqlite3.Error, OSError, ValueError) as error:
                # A telemetry write must not turn a valid PDF delivery into a failure.
                logging.getLogger(__name__).warning("Usage measurement not stored (%s)", type(error).__name__)

    def record(self, sample, *, created=None):
        if sample.operation not in OPERATIONS or type(sample.success) is not bool:
            raise ValueError("La medición tiene un formato inválido.")
        created = self.clock() if created is None else created
        month = datetime.fromtimestamp(created, timezone.utc).strftime("%Y-%m")
        values = (created, month, sample.operation, _identifier_hash(sample.job), _identifier_hash(sample.session),
                  int(sample.success), _quantity(sample.pages, "páginas"), _quantity(sample.fields, "campos"),
                  _quantity(sample.input_bytes, "bytes"), _quantity(sample.output_bytes, "bytes"),
                  _seconds(sample.cpu_seconds), _seconds(sample.elapsed_seconds))
        with self._db() as db:
            db.execute("""INSERT INTO usage_records(created,month,operation,job_hash,session_hash,success,
                pages,fields,input_bytes,output_bytes,cpu_seconds,elapsed_seconds)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", values)

    def set_monthly_actual(self, month, total_usd):
        _month_bounds(month)
        try:
            amount = Decimal(str(total_usd))
        except InvalidOperation as error:
            raise ValueError("Ingresá un costo mensual válido en USD.") from error
        if isinstance(total_usd, bool) or not amount.is_finite() or not 0 <= amount <= 1_000_000:
            raise ValueError("Ingresá un costo mensual válido en USD.")
        cents = int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        with self._db() as db:
            db.execute("""INSERT INTO monthly_actual(month,total_cents,updated) VALUES(?,?,?)
                ON CONFLICT(month) DO UPDATE SET total_cents=excluded.total_cents,updated=excluded.updated""",
                       (month, cents, self.clock()))
        return self.report(month)

    def report(self, month=None):
        month = month or datetime.fromtimestamp(self.clock(), timezone.utc).strftime("%Y-%m")
        start, end = _month_bounds(month)
        with self._db() as db:
            rows = db.execute("""SELECT operation,COUNT(*) AS requests,SUM(success) AS successful,
                SUM(cpu_seconds) AS cpu_seconds,SUM(elapsed_seconds) AS elapsed_seconds,
                SUM(input_bytes) AS input_bytes,SUM(output_bytes) AS output_bytes
                FROM usage_records WHERE month=? GROUP BY operation ORDER BY operation""", (month,)).fetchall()
            documents = db.execute("""SELECT COUNT(*) AS documents,COALESCE(SUM(pages),0) AS pages FROM (
                SELECT job_hash,MAX(pages) AS pages FROM usage_records
                WHERE month=? AND job_hash IS NOT NULL GROUP BY job_hash)""", (month,)).fetchone()
            actual = db.execute("SELECT total_cents FROM monthly_actual WHERE month=?", (month,)).fetchone()
            first = db.execute("SELECT MIN(created) FROM usage_records").fetchone()[0]
        operations = [{"operation": row["operation"], "requests": row["requests"],
                       "successful": row["successful"], "failed": row["requests"] - row["successful"],
                       "cpuSeconds": round(row["cpu_seconds"], 6),
                       "elapsedSeconds": round(row["elapsed_seconds"], 6),
                       "inputBytes": row["input_bytes"], "outputBytes": row["output_bytes"]} for row in rows]
        total = {"operations": sum(item["requests"] for item in operations),
                 "successfulOperations": sum(item["successful"] for item in operations),
                 "failedOperations": sum(item["failed"] for item in operations),
                 "documents": documents["documents"], "pages": documents["pages"],
                 "cpuSeconds": round(sum(row["cpu_seconds"] for row in rows), 6),
                 "elapsedSeconds": round(sum(row["elapsed_seconds"] for row in rows), 6),
                 "inputBytes": sum(item["inputBytes"] for item in operations),
                 "outputBytes": sum(item["outputBytes"] for item in operations),
                 "analysisAttempts": sum(item["requests"] for item in operations if item["operation"] == "detect"),
                 "failedAnalyses": sum(item["failed"] for item in operations if item["operation"] == "detect")}
        cpu_cost = float(Decimal(str(total["cpuSeconds"])) * self.cpu_rate / 60)
        count = total["documents"]
        actual_usd = actual["total_cents"] / 100 if actual else None
        retention_partial = start.timestamp() < self.clock() - 90 * DAY
        return {"month": month, "periodStart": start.isoformat(), "periodEnd": end.isoformat(),
                "periodTimezone": "UTC", "recordsRetentionDays": 90, "actualsRetentionMonths": 24,
                "retentionMayBePartial": retention_partial,
                "measuredSince": datetime.fromtimestamp(first, timezone.utc).isoformat() if first else None,
                "totals": total, "operations": operations,
                "costs": {"currency": "USD", "cpuRatePerMinute": float(self.cpu_rate),
                          "cpuEstimateUSD": round(cpu_cost, 8),
                          "cpuEstimatePerPdfUSD": round(cpu_cost / count, 8) if count else None,
                          "hobbyMinimumUSD": 5,
                          "hobbyBaselinePerPdfUSD": round(5 / count, 8) if count and not retention_partial else None,
                          "actualMonthlyUSD": actual_usd,
                          "fullyAllocatedPerPdfUSD": round(actual_usd / count, 8) if count and actual_usd is not None and not retention_partial else None,
                          "actualSource": "owner_entered" if actual is not None else None,
                          "includes": ["CPU del proceso durante las operaciones medidas"],
                          "excludes": ["RAM", "almacenamiento", "tráfico", "correo", "comisiones", "impuestos"]},
                "limitations": [
                    "La CPU es una estimación parcial, no la factura de Railway ni el costo completo del PDF.",
                    "El costo mensual ingresado se divide por trabajos distintos con actividad medida; incluye gratis, reintentos y fallos en el gasto.",
                    "El escenario Hobby reparte USD 5; no suma otros USD 5 al consumo y no estima el excedente.",
                    "Los meses son calendario UTC; si la factura tiene otro ciclo, hay que asignar el costo al período comparable.",
                    "Los bytes de salida son el tamaño preparado para responder, no prueba de bytes recibidos por el navegador.",
                    "Los datos cubren únicamente operaciones instrumentadas desde la activación, con detalle disponible durante 90 días.",
                ]}

    def cleanup(self):
        now = self.clock()
        current = datetime.fromtimestamp(now, timezone.utc)
        index = current.year * 12 + current.month - 1 - 23
        cutoff_month = f"{index // 12:04d}-{index % 12 + 1:02d}"
        with self._db() as db:
            db.execute("DELETE FROM usage_records WHERE created<?", (now - 90 * DAY,))
            db.execute("DELETE FROM monthly_actual WHERE month<?", (cutoff_month,))
