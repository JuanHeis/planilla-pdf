"""Private export rights, verified email and Mercado Pago Orders."""
from contextlib import closing
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import smtplib
import sqlite3
import ssl
import time
from email.message import EmailMessage
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4

DAY = 86400
PRICE_ARS = 2990
MAX_PAID_PAGES = 5
FREE_MAX_PAGES = 2
TEST_EMAIL_RETENTION = 15 * 60
ORDER_API = "https://api.mercadopago.com/v1/orders"
REASONS = {
    "bad_filled_card_data": "Revisá los datos de la tarjeta e intentá de nuevo.",
    "rejected_by_issuer": "El banco rechazó el pago. Podés probar otro medio.",
    "insufficient_amount": "El medio de pago no tiene saldo suficiente.",
    "card_disabled": "La tarjeta está deshabilitada. Probá otro medio.",
    "required_call_for_authorize": "Tu banco necesita que autorices esta compra.",
    "high_risk": "Mercado Pago no pudo aprobar esta operación. Probá otro medio.",
    "processing_error": "El proveedor tuvo un problema temporal. Podés reintentar.",
    "canceled": "El pago se canceló o venció. Podés volver a intentarlo.",
}


class CommerceError(ValueError):
    def __init__(self, message, status=400, code="invalid_request"):
        super().__init__(message)
        self.status = status
        self.code = code


class _Connection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


def request_json(method, url, headers, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    request = Request(url, data=data, headers={**headers, "Content-Type": "application/json"}, method=method)
    try:
        with urlopen(request, timeout=12) as response:
            return json.load(response)
    except (HTTPError, URLError, TimeoutError, UnicodeError, json.JSONDecodeError) as error:
        raise CommerceError("El proveedor no respondió. Podés volver a intentar sin perder tu trabajo.",
                            503, "provider_unavailable") from error


class Commerce:
    def __init__(self, data_dir=None, http_request=None, clock=None):
        self.data_dir = Path(data_dir or os.environ.get("PLANILLA_DATA_DIR", os.environ.get("DATA_DIR", "data"))).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.jobs_dir = self.data_dir / "jobs"
        self.jobs_dir.mkdir(exist_ok=True)
        self.database = self.data_dir / "commerce.sqlite3"
        self.http = http_request or request_json
        self.clock = clock or time.time
        self.base_url = os.environ.get("PUBLIC_BASE_URL", "http://127.0.0.1:8765").rstrip("/")
        self.access_token = os.environ.get("MERCADOPAGO_ACCESS_TOKEN", "")
        self.webhook_secret = os.environ.get("MERCADOPAGO_WEBHOOK_SECRET", "")
        self.collector = os.environ.get("MERCADOPAGO_COLLECTOR_ID", "")
        environment = os.environ.get("APP_ENV", "").lower()
        self.production = environment == "production"
        test_email = os.environ.get("PLANILLA_TEST_EMAIL", "0")
        if test_email not in {"0", "1"}:
            raise RuntimeError("PLANILLA_TEST_EMAIL debe ser 0 o 1.")
        if test_email == "1" and environment != "test":
            raise RuntimeError("La bandeja de pruebas requiere APP_ENV=test.")
        self.test_email_enabled = test_email == "1"
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS people (
                    email TEXT PRIMARY KEY, verified INTEGER NOT NULL DEFAULT 0, free_job TEXT UNIQUE);
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, created REAL NOT NULL, touched REAL NOT NULL,
                    email TEXT REFERENCES people(email));
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, session TEXT NOT NULL REFERENCES sessions(id),
                    pages INTEGER NOT NULL, existing_count INTEGER NOT NULL DEFAULT 0,
                    created REAL NOT NULL, expires REAL NOT NULL, prepared_sha TEXT,
                    original_mode INTEGER NOT NULL DEFAULT 0, grant_kind TEXT,
                    granted REAL, edit_until REAL, last_order TEXT, email_sent REAL, grant_order TEXT,
                    owner_email TEXT);
                CREATE TABLE IF NOT EXISTS email_tokens (
                    digest TEXT PRIMARY KEY, session TEXT NOT NULL, job TEXT NOT NULL,
                    email TEXT NOT NULL, created REAL NOT NULL, verified REAL,
                    purpose TEXT NOT NULL DEFAULT 'verify');
                CREATE TABLE IF NOT EXISTS test_mail (
                    id INTEGER PRIMARY KEY, recipient TEXT NOT NULL, subject TEXT NOT NULL,
                    text TEXT NOT NULL, verification_url TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS orders (
                    reference TEXT PRIMARY KEY, job TEXT NOT NULL, provider_id TEXT UNIQUE,
                    checkout_url TEXT, state TEXT NOT NULL, detail TEXT, created REAL NOT NULL,
                    last_checked REAL, provider_updated TEXT, idempotency TEXT NOT NULL, amount TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS claims (
                    id TEXT PRIMARY KEY, job TEXT NOT NULL, created REAL NOT NULL,
                    reason TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'requested', order_reference TEXT);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY, event TEXT NOT NULL, created REAL NOT NULL, session TEXT, job TEXT);
                CREATE INDEX IF NOT EXISTS jobs_by_session ON jobs(session);
                CREATE INDEX IF NOT EXISTS tokens_by_email ON email_tokens(email, created);
                CREATE INDEX IF NOT EXISTS events_by_date ON events(created);
            """)
            for table, column in (("jobs", "grant_order"), ("jobs", "owner_email"), ("claims", "order_reference")):
                if column not in {row[1] for row in db.execute(f"PRAGMA table_info({table})")}:
                    db.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT")
            if "purpose" not in {row[1] for row in db.execute("PRAGMA table_info(email_tokens)")}:
                db.execute("ALTER TABLE email_tokens ADD COLUMN purpose TEXT NOT NULL DEFAULT 'verify'")
            db.execute("""UPDATE jobs SET owner_email=(SELECT sessions.email FROM sessions
                JOIN people ON people.email=sessions.email AND people.verified=1
                WHERE sessions.id=jobs.session) WHERE owner_email IS NULL""")
            db.execute("CREATE INDEX IF NOT EXISTS jobs_by_owner ON jobs(owner_email,created)")
            self._clean_test_mail(db)

    def _db(self):
        db = sqlite3.connect(self.database, timeout=10, factory=_Connection)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        return db

    def _event(self, db, event, session=None, job=None):
        db.execute("INSERT INTO events(event,created,session,job) VALUES(?,?,?,?)", (event, self.clock(), session, job))

    def event(self, session, event, job=None, details=None):
        if event not in {"page_view", "editor_open", "visit", "upload_started", "detected", "detection_failed", "preview_ready", "already_editable", "editable_detected", "prepare_failed", "download_clicked", "error", "corrected", "original_download"}:
            raise CommerceError("Evento inválido.")
        with self._db() as db:
            self._event(db, event, session, job)

    def ensure_session(self, cookie):
        now = self.clock()
        with self._db() as db:
            if isinstance(cookie, str) and re.fullmatch(r"[A-Za-z0-9_-]{43}", cookie):
                row = db.execute("SELECT * FROM sessions WHERE id=? AND touched>?", (cookie, now - 30 * DAY)).fetchone()
                if row:
                    db.execute("UPDATE sessions SET touched=? WHERE id=?", (now, cookie))
                    return cookie
            key = secrets.token_urlsafe(32)
            db.execute("INSERT INTO sessions(id,created,touched) VALUES(?,?,?)", (key, now, now))
            self._event(db, "visit", key)
            return key

    def _job(self, db, session, job, allow_expired=False):
        row = db.execute("SELECT * FROM jobs WHERE id=?", (job,)).fetchone()
        person = self._email(db, session) if row and row["session"] != session else None
        shared = bool(person and person["verified"] and person["email"] == row["owner_email"])
        if row is None or (row["session"] != session and not shared):
            raise CommerceError("No encontramos ese trabajo en esta sesión.", 404, "job_not_found")
        if not allow_expired and row["expires"] <= self.clock():
            raise CommerceError("Este trabajo venció. Podés volver a subir el PDF.", 410, "expired")
        return row

    def authorize(self, session, job):
        with closing(self._db()) as db:
            return dict(self._job(db, session, job))

    def jobs(self, session):
        with closing(self._db()) as db:
            person = self._email(db, session)
            if not person or not person["verified"]:
                return []
            rows = db.execute("""SELECT id,created,pages FROM jobs WHERE owner_email=? AND expires>?
                ORDER BY created DESC,id DESC LIMIT 100""", (person["email"], self.clock())).fetchall()
        result = []
        for row in rows:
            status = self.status(session, row["id"])
            result.append({"id": row["id"], "jobId": row["id"], "created": row["created"],
                           "pages": row["pages"], **{key: status[key] for key in (
                               "expiresAt", "prepared", "state", "grantKind", "downloadUrl", "message")}})
        return result

    def register_job(self, session, job, pages, existing_count=0):
        if not isinstance(job, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,80}", job):
            raise CommerceError("Identificador de trabajo inválido.")
        if type(pages) is not int or not 1 <= pages <= 50 or type(existing_count) is not int or existing_count < 0:
            raise CommerceError("El documento no tiene una cantidad de páginas válida.")
        now = self.clock()
        with self._db() as db:
            if not db.execute("SELECT 1 FROM sessions WHERE id=?", (session,)).fetchone():
                raise CommerceError("La sesión venció. Volvé a abrir la página.", 403, "session_expired")
            count = db.execute("SELECT COUNT(*) FROM jobs WHERE session=? AND created>?", (session, now - 3600)).fetchone()[0]
            if count >= 20:
                raise CommerceError("Llegaste al límite de archivos por hora. Intentá más tarde.", 429, "upload_limit")
            person = self._email(db, session)
            owner = person["email"] if person and person["verified"] else None
            db.execute("INSERT INTO jobs(id,session,pages,existing_count,created,expires,owner_email) VALUES(?,?,?,?,?,?,?)",
                       (job, session, pages, existing_count, now, now + 7 * DAY, owner))
            self._event(db, "preview_ready", session, job)
            if existing_count:
                self._event(db, "editable_detected", session, job)

    def prepare(self, session, job, pdf_bytes, original=False):
        if not isinstance(pdf_bytes, bytes) or not pdf_bytes.startswith(b"%PDF-"):
            raise CommerceError("No pudimos validar el PDF para descargar.", 400, "invalid_pdf")
        digest = sha256(pdf_bytes).hexdigest()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._job(db, session, job)
            if original and not row["existing_count"]:
                raise CommerceError("Este PDF no tiene controles originales.", 400, "not_editable")
            if not original and row["pages"] > MAX_PAID_PAGES:
                raise CommerceError("Por ahora convertimos PDFs de hasta 5 páginas. Tu prueba gratis sigue disponible.", 400, "page_limit")
            if row["edit_until"] and row["edit_until"] <= self.clock() and row["prepared_sha"] != digest:
                raise CommerceError("Terminó el período para cambiar este trabajo. La descarga anterior sigue disponible.", 409, "editing_expired")
            if row["last_order"] and row["prepared_sha"] != digest:
                order = db.execute("SELECT state FROM orders WHERE reference=?", (row["last_order"],)).fetchone()
                if order and order["state"] in {"creating", "created", "processing", "action_required"}:
                    raise CommerceError("Hay un pago pendiente. Esperá su resultado antes de cambiar el PDF.", 409, "payment_pending")
            directory = self.jobs_dir / job
            directory.mkdir(exist_ok=True)
            temporary = directory / ("final-" + uuid4().hex + ".tmp")
            try:
                temporary.write_bytes(pdf_bytes)
                os.replace(temporary, directory / "final.pdf")
            finally:
                temporary.unlink(missing_ok=True)
            db.execute("UPDATE jobs SET prepared_sha=?,original_mode=? WHERE id=?", (digest, int(bool(original)), job))
            self._event(db, "prepared", session, job)
        return self.status(session, job)

    def _email(self, db, session):
        row = db.execute("SELECT people.* FROM sessions LEFT JOIN people ON people.email=sessions.email WHERE sessions.id=?", (session,)).fetchone()
        return row if row and row["email"] else None

    def status(self, session, job):
        with closing(self._db()) as db:
            row = self._job(db, session, job, allow_expired=True)
            person = self._email(db, session)
            order_reference = row["grant_order"] if row["grant_kind"] == "paid" else row["last_order"]
            order = db.execute("SELECT * FROM orders WHERE reference=?", (order_reference,)).fetchone() if order_reference else None
            claim = db.execute("SELECT state FROM claims WHERE job=? ORDER BY created DESC LIMIT 1", (job,)).fetchone()
        now = self.clock()
        free = bool(person and person["verified"] and person["free_job"] in (None, job) and row["pages"] <= FREE_MAX_PAGES)
        ready = row["prepared_sha"] is not None
        grant = row["grant_kind"]
        state, message = "needs_preparation", "Revisá los campos y prepará la descarga. Tu gratuito sigue disponible."
        if row["expires"] <= now:
            state, message = "expired", "Este trabajo venció. Podés volver a subir el PDF."
        elif ready and row["original_mode"]:
            state, message = "download_ready", "El PDF ya era rellenable. Podés descargarlo sin usar tu gratuito."
        elif order and order["state"] == "refunded":
            state, message = "refunded", "Mercado Pago confirmó la devolución del pago."
        elif ready and grant in {"paid", "free"}:
            state, message = "download_ready", "Tu PDF está listo. Podés volver a descargar este trabajo sin otro cobro."
        elif ready and grant == "free_pending" and free:
            state, message = "download_ready", "Tu descarga gratis está lista. Se usará al descargar este trabajo."
        elif not ready:
            pass
        elif not person or not person["verified"]:
            state = "email_pending" if row["email_sent"] and row["email_sent"] > now - 900 else "needs_email"
            message = "Te enviamos un enlace. Confirmá tu correo para continuar. Tu gratuito sigue disponible." if state == "email_pending" else "Ingresá tu correo para descargar y recuperar este trabajo."
            if state == "email_pending" and self.test_email_enabled:
                message = "El enlace está en la bandeja de pruebas del administrador. No se envió un correo real. Tu gratuito sigue disponible."
        elif order and order["state"] in {"creating", "created", "processing", "action_required", "processed"}:
            state, message = "payment_pending", "El pago está pendiente. Tu trabajo está guardado. Evitá pagar otra vez mientras lo confirmamos."
        elif order and order["state"] in {"failed", "canceled"}:
            state, message = "payment_rejected", REASONS.get(order["detail"], "El pago no se aprobó. Podés probar otro medio. Tu trabajo está guardado.")
        elif free:
            state, message = "free_available", "Tenés una conversión gratis de hasta 2 páginas. Todavía no se consumió."
        else:
            state, message = "payment_required", "Tu PDF está preparado. El pago único por este trabajo es de ARS 2.990."
        return {
            "state": state, "message": message, "jobId": job, "freeAvailable": free,
            "alreadyEditable": bool(row["existing_count"]), "originalAvailable": bool(row["existing_count"]),
            "prepared": ready, "email": person["email"] if person else None,
            "emailVerified": bool(person and person["verified"]), "price": {"amount": PRICE_ARS, "currency": "ARS"},
            "downloadUrl": f"/api/jobs/{job}/download" if state == "download_ready" else None,
            "checkoutUrl": order["checkout_url"] if order and state == "payment_pending"
                and self._payment_environment_matches(order["provider_id"])
                and self._checkout_url_matches(order["provider_id"], order["checkout_url"]) else None,
            "expiresAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(row["expires"])),
            "reasonCode": order["detail"] if order else None, "claimStatus": claim["state"] if claim else None,
            "canClaim": bool(grant == "paid" and row["expires"] > now and not claim),
            "grantKind": "original" if row["original_mode"] else grant,
            "quotaConsumed": bool(person and person["free_job"] is not None),
        }

    @staticmethod
    def _normalize_email(value):
        if not isinstance(value, str):
            raise CommerceError("Ingresá un correo válido.", 400, "invalid_email")
        value = value.strip().casefold()
        if len(value) > 254 or not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", value):
            raise CommerceError("Ingresá un correo válido.", 400, "invalid_email")
        return value

    def _clean_test_mail(self, db):
        if self.test_email_enabled:
            db.execute("DELETE FROM test_mail WHERE created<=?", (self.clock() - TEST_EMAIL_RETENTION,))
            db.execute("DELETE FROM test_mail WHERE id NOT IN (SELECT id FROM test_mail ORDER BY id DESC LIMIT 100)")
        else:
            db.execute("DELETE FROM test_mail")

    def test_mailbox(self):
        if not self.test_email_enabled:
            raise CommerceError("La bandeja de pruebas no está habilitada.", 404, "test_mailbox_disabled")
        with self._db() as db:
            self._clean_test_mail(db)
            messages = [dict(row) for row in db.execute("""SELECT id,recipient AS 'to',subject,text,
                verification_url AS verificationUrl,created FROM test_mail ORDER BY id DESC LIMIT 100""")]
        return {"enabled": True, "retentionSeconds": TEST_EMAIL_RETENTION, "messages": messages}

    def _mail(self, email, token, db):
        link = self.base_url + "/verify?" + urlencode({"token": token})
        subject = "Tu enlace para continuar en Planilla"
        text = ("Confirmá tu correo y recuperá tu trabajo en Planilla:\n\n" + link +
                "\n\nLa primera confirmación vence en 15 minutos. Después podés recuperar el trabajo durante 7 días."
                "\nConfirmar el correo no consume la descarga gratuita.")
        if self.test_email_enabled:
            db.execute("INSERT INTO test_mail(recipient,subject,text,verification_url,created) VALUES(?,?,?,?,?)",
                       (email, "Prueba · " + subject, "PRUEBA: no se envió un correo real.\n\n" + text, link, self.clock()))
            self._clean_test_mail(db)
            return
        key = os.environ.get("RESEND_API_KEY", "")
        sender = os.environ.get("EMAIL_FROM", os.environ.get("SMTP_USERNAME", ""))
        if key and sender:
            return self.http("POST", "https://api.resend.com/emails", {"Authorization": "Bearer " + key}, {
                "from": sender, "to": [email], "subject": subject, "text": text,
            })
        username, password = os.environ.get("SMTP_USERNAME", ""), os.environ.get("SMTP_PASSWORD", "")
        if not username or not password or not sender:
            raise CommerceError("El correo todavía no está habilitado. Tu gratuito no se consumió.", 503, "email_unconfigured")
        message = EmailMessage()
        message["From"], message["To"], message["Subject"] = sender, email, subject
        message.set_content(text)
        try:
            with smtplib.SMTP_SSL(os.environ.get("SMTP_HOST", "smtp.gmail.com"),
                                  int(os.environ.get("SMTP_PORT", "465")), timeout=12,
                                  context=ssl.create_default_context()) as smtp:
                smtp.login(username, password)
                smtp.send_message(message)
        except (smtplib.SMTPException, OSError, ValueError) as error:
            raise CommerceError("No pudimos enviar el correo. Tu gratuito no se consumió. Volvé a intentar.", 503, "email_unavailable") from error

    def _new_token(self, db, session, job, email, purpose="verify"):
        now = self.clock()
        recent = db.execute("SELECT created FROM email_tokens WHERE email=? ORDER BY created DESC LIMIT 1", (email,)).fetchone()
        if recent and recent["created"] > now - 60:
            raise CommerceError("Esperá un minuto antes de pedir otro enlace. Revisá también el correo no deseado.", 429, "email_rate_limit")
        total = db.execute("SELECT COUNT(*) FROM email_tokens WHERE session=? AND created>?", (session, now - 3600)).fetchone()[0]
        if total >= 5:
            raise CommerceError("Llegaste al límite de correos por hora. Intentá más tarde.", 429, "email_rate_limit")
        token = secrets.token_urlsafe(32)
        db.execute("INSERT INTO email_tokens(digest,session,job,email,created,purpose) VALUES(?,?,?,?,?,?)",
                   (sha256(token.encode()).hexdigest(), session, job, email, now, purpose))
        return token

    def request_email(self, session, job, email):
        email = self._normalize_email(email)
        with self._db() as db:
            job_row = self._job(db, session, job)
            if job_row["owner_email"] and job_row["owner_email"] != email:
                raise CommerceError("Este trabajo está vinculado a otro correo. Recuperalo con ese correo o abrí una sesión nueva.",
                                    409, "email_mismatch")
            token = self._new_token(db, session, job, email)
            self._mail(email, token, db)
            db.execute("UPDATE jobs SET email_sent=? WHERE id=?", (self.clock(), job))
            self._event(db, "email_requested", session, job)
        return self.status(session, job)

    def verify(self, token):
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            raise CommerceError("El enlace de correo es inválido o venció.", 400, "invalid_token")
        now = self.clock()
        with self._db() as db:
            row = db.execute("SELECT * FROM email_tokens WHERE digest=?", (sha256(token.encode()).hexdigest(),)).fetchone()
            if not row or now - row["created"] > (7 * DAY if row["verified"] is not None else 900):
                raise CommerceError("El enlace venció. Pedí uno nuevo para continuar.", 410, "expired_token")
            job = db.execute("SELECT * FROM jobs WHERE id=? AND expires>?", (row["job"], now)).fetchone()
            if job is None or (job["owner_email"] and job["owner_email"] != row["email"]):
                raise CommerceError("El enlace no corresponde a este trabajo o venció. Pedí uno nuevo.", 410, "expired_token")
            session = row["session"]
            recovery = row["purpose"] == "recover" or row["verified"] is not None
            if recovery:
                if job["owner_email"] != row["email"]:
                    raise CommerceError("El enlace venció. Pedí uno nuevo para continuar.", 410, "expired_token")
                # A recovery link proves the email, never possession of the original browser.
                session = secrets.token_urlsafe(32)
            else:
                self._job(db, session, row["job"])
            db.execute("INSERT INTO people(email,verified) VALUES(?,1) ON CONFLICT(email) DO UPDATE SET verified=1", (row["email"],))
            if recovery:
                db.execute("INSERT INTO sessions(id,created,touched,email) VALUES(?,?,?,?)", (session, now, now, row["email"]))
            else:
                db.execute("UPDATE sessions SET email=?,touched=? WHERE id=?", (row["email"], now, session))
                db.execute("UPDATE jobs SET owner_email=? WHERE session=? AND owner_email IS NULL", (row["email"], session))
            db.execute("UPDATE email_tokens SET verified=COALESCE(verified,?) WHERE digest=?", (now, row["digest"]))
            if row["verified"] is None:
                self._event(db, "email_verified", session, row["job"])
            return {"sessionId": session, "jobId": row["job"]}

    def recover(self, email):
        email = self._normalize_email(email)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""SELECT jobs.* FROM jobs
                WHERE (jobs.owner_email=? OR (jobs.owner_email IS NULL AND EXISTS (
                    SELECT 1 FROM email_tokens WHERE email_tokens.session=jobs.session
                    AND email_tokens.job=jobs.id AND email_tokens.email=?)))
                AND jobs.expires>? AND jobs.prepared_sha IS NOT NULL
                ORDER BY jobs.created DESC LIMIT 1""", (email, email, self.clock())).fetchone()
            if row:
                try:
                    token = self._new_token(db, row["session"], row["id"], email,
                                            purpose="recover" if row["owner_email"] else "verify")
                    self._mail(email, token, db)
                except CommerceError as error:
                    if error.status != 429:
                        raise
        return {"message": "Si hay un trabajo disponible para ese correo, su enlace estará en la bandeja de pruebas del administrador. No se envió un correo real." if self.test_email_enabled else "Si hay un trabajo disponible para ese correo, recibirás un enlace para recuperarlo."}

    def claim_free(self, session, job):
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._job(db, session, job)
            person = self._email(db, session)
            if not row["prepared_sha"]:
                raise CommerceError("Prepará el PDF antes de continuar.", 409, "needs_preparation")
            if not person or not person["verified"]:
                raise CommerceError("Confirmá tu correo para usar la descarga gratis.", 403, "needs_email")
            if row["pages"] > FREE_MAX_PAGES or person["free_job"] not in (None, job):
                raise CommerceError("La prueba gratis no está disponible para este trabajo.", 409, "free_unavailable")
            if row["grant_kind"] != "paid":
                db.execute("UPDATE jobs SET grant_kind=COALESCE(grant_kind,'free_pending') WHERE id=?", (job,))
        return self.status(session, job)

    def download(self, session, job):
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._job(db, session, job)
            path = self.jobs_dir / job / "final.pdf"
            if not row["prepared_sha"] or not path.is_file():
                raise CommerceError("El archivo todavía no está preparado. Volvé a preparar la descarga.", 409, "needs_preparation")
            person = self._email(db, session)
            if row["original_mode"] and row["existing_count"]:
                self._event(db, "original_download", session, job)
                return path
            if row["grant_kind"] == "free_pending":
                if not person or not person["verified"] or row["pages"] > FREE_MAX_PAGES:
                    raise CommerceError("La descarga gratis requiere un correo confirmado.", 403, "needs_email")
                if person["free_job"] not in (None, job):
                    raise CommerceError("Tu descarga gratis ya se usó en otro trabajo.", 403, "free_unavailable")
                db.execute("UPDATE people SET free_job=? WHERE email=? AND free_job IS NULL", (job, person["email"]))
                now = self.clock()
                db.execute("UPDATE jobs SET grant_kind='free',granted=?,edit_until=?,expires=MAX(expires,?) WHERE id=?",
                           (now, now + DAY, now + 7 * DAY, job))
                self._event(db, "free_redeemed", session, job)
            elif row["grant_kind"] not in {"paid", "free"}:
                raise CommerceError("Confirmá el pago o reclamá tu descarga gratis para continuar.", 403, "payment_required")
            self._event(db, "download", session, job)
            return path

    def _mp_headers(self, idempotency=None):
        if not self.access_token or not self.collector:
            raise CommerceError("Los pagos todavía no están habilitados. Tu trabajo está guardado.", 503, "payment_unconfigured")
        headers = {"Authorization": "Bearer " + self.access_token}
        if idempotency:
            headers["X-Idempotency-Key"] = idempotency
        return headers

    def _payment_environment_matches(self, identifier, live_mode=None):
        if not isinstance(identifier, str):
            return False
        test_order = identifier.startswith("ORDTST")
        return (test_order != self.production
                and (live_mode if isinstance(live_mode, bool) else not test_order) == self.production)

    def _require_payment_environment(self, identifier, live_mode=None):
        if not self._payment_environment_matches(identifier, live_mode):
            raise CommerceError("No pudimos abrir el pago con la configuración actual. Tu trabajo está guardado.",
                                502, "payment_environment_mismatch")

    @staticmethod
    def _checkout_url_matches(identifier, url):
        if not isinstance(url, str):
            return False
        try:
            parsed = urlsplit(url)
            return (parsed.scheme == "https" and parsed.hostname == "www.mercadopago.com.ar"
                    and not parsed.username and not parsed.password and parsed.port in (None, 443)
                    and parse_qs(parsed.query).get("order_id") == [identifier])
        except ValueError:
            return False

    def checkout(self, session, job):
        if not self.webhook_secret:
            raise CommerceError("Los pagos todavía no están habilitados. Tu trabajo está guardado.", 503, "payment_unconfigured")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._job(db, session, job)
            person = self._email(db, session)
            if not row["prepared_sha"]:
                raise CommerceError("Prepará el PDF antes de pagar.", 409, "needs_preparation")
            if row["pages"] > MAX_PAID_PAGES:
                raise CommerceError("Por ahora convertimos PDFs de hasta 5 páginas.", 400, "page_limit")
            if not person or not person["verified"]:
                raise CommerceError("Confirmá tu correo para continuar.", 403, "needs_email")
            if row["grant_kind"] in {"paid", "free"} or row["original_mode"]:
                raise CommerceError("Este trabajo ya se puede descargar. No hace falta otro pago.", 409, "already_granted")
            previous = db.execute("SELECT * FROM orders WHERE reference=?", (row["last_order"],)).fetchone() if row["last_order"] else None
            if previous and previous["state"] not in {"failed", "canceled", "refunded"}:
                reference, idempotency = previous["reference"], previous["idempotency"]
                if previous["checkout_url"]:
                    self._require_payment_environment(previous["provider_id"])
                    if not self._checkout_url_matches(previous["provider_id"], previous["checkout_url"]):
                        raise CommerceError("Mercado Pago no devolvió un enlace válido. Tu trabajo está guardado.", 502, "invalid_checkout")
                    return {**self.status(session, job), "checkoutUrl": previous["checkout_url"], "orderId": previous["provider_id"]}
            else:
                reference, idempotency = uuid4().hex, uuid4().hex
                db.execute("INSERT INTO orders(reference,job,state,created,idempotency,amount) VALUES(?,?,'creating',?,?,?)",
                           (reference, job, self.clock(), idempotency, str(PRICE_ARS)))
                db.execute("UPDATE jobs SET last_order=? WHERE id=?", (reference, job))
        # Persist and reuse the same key when a provider timeout makes the result uncertain.
        result = self.http("POST", ORDER_API, self._mp_headers(idempotency), {
            "type": "online", "processing_mode": "manual", "capture_mode": "automatic_async",
            "total_amount": f"{PRICE_ARS:.2f}", "external_reference": reference,
            "expiration_time": "P1D", "payer": {"email": person["email"]},
            "items": [{"title": "Planilla · conversión PDF hasta 5 páginas", "unit_price": f"{PRICE_ARS:.2f}", "quantity": 1}],
            "config": {"online": {**{name + "_url": self.base_url + "/?job=" + job for name in ("success", "pending", "failure")}, "auto_return": "all"}},
        })
        provider_id, checkout_url = result.get("id"), result.get("checkout_url")
        if (not isinstance(provider_id, str) or not re.fullmatch(r"[A-Za-z0-9]{8,100}", provider_id)
                or not self._checkout_url_matches(provider_id, checkout_url)):
            raise CommerceError("Mercado Pago no devolvió un enlace válido. Tu trabajo está guardado.", 502, "invalid_checkout")
        self._require_payment_environment(provider_id, result.get("live_mode"))
        with self._db() as db:
            db.execute("UPDATE orders SET provider_id=?,checkout_url=?,state='created' WHERE reference=? AND state='creating'",
                       (provider_id, checkout_url, reference))
            self._event(db, "checkout_started", session, job)
        return {**self.status(session, job), "checkoutUrl": checkout_url, "orderId": provider_id}

    def _validate_webhook(self, query, headers):
        if not self.webhook_secret:
            raise CommerceError("La recepción de pagos no está configurada.", 503, "webhook_unconfigured")
        identifier = query.get("data.id") or query.get("data_id")
        request_id = headers.get("x-request-id") or headers.get("X-Request-Id")
        signature = headers.get("x-signature") or headers.get("X-Signature") or ""
        parts = dict(item.strip().split("=", 1) for item in signature.split(",") if "=" in item)
        timestamp, digest = parts.get("ts", ""), parts.get("v1", "")
        if (not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9]{8,100}", identifier)
                or not isinstance(request_id, str) or not 1 <= len(request_id) <= 150
                or not timestamp.isdigit() or not re.fullmatch(r"[0-9a-f]{64}", digest)):
            raise CommerceError("Notificación de pago inválida.", 401, "invalid_signature")
        manifest = f"id:{identifier.lower()};request-id:{request_id};ts:{timestamp};"
        expected = hmac.new(self.webhook_secret.encode(), manifest.encode(), sha256).hexdigest()
        if not hmac.compare_digest(expected, digest):
            raise CommerceError("Notificación de pago inválida.", 401, "invalid_signature")
        # Provider retries can be old. Authenticated API state determines the current right.
        return identifier

    def process_webhook(self, query, headers, body=None):
        provider_id = self._validate_webhook(query, headers)
        with closing(self._db()) as db:
            local = db.execute("SELECT * FROM orders WHERE provider_id=?", (provider_id,)).fetchone()
        if local is None:
            return {"received": True}
        result = self.http("GET", ORDER_API + "/" + provider_id, self._mp_headers())
        self._apply_order(local["reference"], result)
        return {"received": True}

    def refresh(self, session, job):
        with closing(self._db()) as db:
            row = self._job(db, session, job)
            order = db.execute("SELECT * FROM orders WHERE reference=?", (row["last_order"],)).fetchone() if row["last_order"] else None
        if row["grant_kind"] in {"paid", "free"} or row["original_mode"]:
            return self.status(session, job)
        if (order and order["provider_id"] and order["state"] not in {"failed", "canceled", "refunded"}
                and (not order["last_checked"] or order["last_checked"] < self.clock() - 10)):
            try:
                result = self.http("GET", ORDER_API + "/" + order["provider_id"], self._mp_headers())
                # Pending UI updates are useful, but only the signed webhook grants paid export.
                self._apply_order(order["reference"], result, grant=False)
            except CommerceError as error:
                if error.code != "provider_unavailable":
                    raise
                status = self.status(session, job)
                status["message"] = "El pago sigue pendiente. Mercado Pago no respondió; reintentaremos la consulta. Tu trabajo está guardado."
                return status
        return self.status(session, job)

    def _apply_order(self, reference, result, grant=True):
        if not isinstance(result, dict):
            raise CommerceError("La orden recibida no es válida.", 400, "order_mismatch")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            local = db.execute("SELECT * FROM orders WHERE reference=?", (reference,)).fetchone()
            if local is None:
                raise CommerceError("No encontramos esa orden.", 404, "order_not_found")
            try:
                valid_amount = Decimal(str(result.get("total_amount"))) == Decimal(local["amount"])
                paid_amount = Decimal(str(result.get("total_paid_amount", "0")))
            except (InvalidOperation, TypeError) as error:
                raise CommerceError("La orden recibida tiene un importe inválido.", 400, "order_mismatch") from error
            if (result.get("id") != local["provider_id"] or result.get("external_reference") != reference
                    or not valid_amount or result.get("currency") != "ARS"
                    or str(result.get("user_id")) != str(self.collector)
                    or not self._payment_environment_matches(result.get("id"), result.get("live_mode"))):
                raise CommerceError("La orden recibida no coincide con la compra.", 400, "order_mismatch")
            state, detail = result.get("status"), result.get("status_detail")
            if state not in {"created", "processed", "action_required", "failed", "processing", "refunded", "canceled"}:
                raise CommerceError("El proveedor devolvió un estado desconocido.", 400, "unknown_order_state")
            if state == "processed" and detail == "accredited" and paid_amount != Decimal(local["amount"]):
                raise CommerceError("El pago acreditado no coincide con el importe.", 400, "order_mismatch")
            updated = result.get("last_updated_date")
            if not isinstance(updated, str):
                raise CommerceError("La orden no tiene fecha de actualización.", 400, "order_mismatch")
            if local["provider_updated"] and updated < local["provider_updated"]:
                return
            previous = local["state"]
            if previous == "refunded" and state != "refunded":
                return
            if previous == "processed" and state not in {"processed", "refunded"}:
                return
            db.execute("UPDATE orders SET state=?,detail=?,last_checked=?,provider_updated=? WHERE reference=?",
                       (state, detail, self.clock(), updated, reference))
            job = db.execute("SELECT * FROM jobs WHERE id=?", (local["job"],)).fetchone()
            if state == "refunded":
                db.execute("UPDATE jobs SET grant_kind=NULL,grant_order=NULL WHERE id=? AND grant_kind='paid' AND grant_order=?", (job["id"], reference))
                db.execute("UPDATE claims SET state='refunded' WHERE order_reference=?", (reference,))
                if previous != "refunded":
                    self._event(db, "payment_refunded", job["session"], job["id"])
            elif state == "processed" and detail == "accredited" and grant:
                if job["grant_kind"] != "paid":
                    now = self.clock()
                    db.execute("UPDATE jobs SET grant_kind='paid',grant_order=?,granted=?,edit_until=?,expires=MAX(expires,?) WHERE id=?",
                               (reference, now, now + DAY, now + 7 * DAY, job["id"]))
                    self._event(db, "payment_approved", job["session"], job["id"])
            elif state == "failed" and previous != "failed":
                self._event(db, "payment_rejected", job["session"], job["id"])

    def claim(self, session, job, reason):
        if (not isinstance(reason, str) or not 10 <= len(reason.strip()) <= 2000
                or not all(character.isprintable() or character in "\n\r\t" for character in reason)):
            raise CommerceError("Contanos qué salió mal en entre 10 y 2000 caracteres.", 400, "invalid_claim")
        with self._db() as db:
            row = self._job(db, session, job)
            if row["grant_kind"] != "paid":
                raise CommerceError("Este trabajo no tiene un pago para reclamar.", 400, "claim_unavailable")
            if not db.execute("SELECT id FROM claims WHERE job=?", (job,)).fetchone():
                db.execute("INSERT INTO claims(id,job,created,reason,order_reference) VALUES(?,?,?,?,?)", (uuid4().hex, job, self.clock(), reason.strip(), row["grant_order"]))
                self._event(db, "claim_requested", session, job)
        return self.status(session, job)

    def refund(self, claim_id):
        with closing(self._db()) as db:
            claim = db.execute("""SELECT claims.*,orders.reference,orders.provider_id FROM claims
                JOIN orders ON orders.reference=claims.order_reference
                WHERE claims.id=?""", (claim_id,)).fetchone()
        if not claim or not claim["provider_id"]:
            raise CommerceError("No encontramos ese reclamo con pago.", 404, "claim_not_found")
        if claim["state"] == "refunded":
            return {"state": "refunded", "message": "La devolución ya está confirmada."}
        self.http("POST", ORDER_API + "/" + claim["provider_id"] + "/refund",
                  self._mp_headers("refund-" + claim["id"]))
        with self._db() as db:
            db.execute("UPDATE claims SET state='refund_requested' WHERE id=? AND state!='refunded'", (claim_id,))
        # Do not call an accepted request a completed refund until the provider confirms it.
        result = self.http("GET", ORDER_API + "/" + claim["provider_id"], self._mp_headers())
        self._apply_order(claim["reference"], result)
        with closing(self._db()) as db:
            state = db.execute("SELECT state FROM claims WHERE id=?", (claim_id,)).fetchone()[0]
        return {"state": state, "message": "La devolución está confirmada." if state == "refunded" else "Mercado Pago recibió la devolución. Estamos esperando la confirmación."}

    def metrics(self):
        with closing(self._db()) as db:
            since = self.clock() - 30 * DAY
            # One document in the funnel stays one document after retries/exports.
            count_key = "CASE WHEN event IN ('prepared','download','original_download','detected','preview_ready','corrected') THEN job ELSE CAST(id AS TEXT) END"
            counts = dict(db.execute(f"SELECT event,COUNT(DISTINCT {count_key}) FROM events WHERE created>=? GROUP BY event", (since,)))
            daily = [dict(row) for row in db.execute(f"SELECT date(created,'unixepoch') AS day,event,COUNT(DISTINCT {count_key}) AS count FROM events WHERE created>=? GROUP BY day,event ORDER BY day", (since,))]
            claims = db.execute("SELECT COUNT(*) FROM claims WHERE state IN ('requested','refund_requested')").fetchone()[0]
        return {"periodDays": 30, "counts": counts, "daily": daily, "openClaims": claims,
                "grossApprovedARS": counts.get("payment_approved", 0) * PRICE_ARS,
                "refundedARS": counts.get("payment_refunded", 0) * PRICE_ARS}

    def claims(self):
        with closing(self._db()) as db:
            return [dict(row) for row in db.execute("""SELECT claims.*,jobs.owner_email AS email,orders.provider_id
                FROM claims JOIN jobs ON jobs.id=claims.job
                LEFT JOIN orders ON orders.reference=claims.order_reference ORDER BY claims.created DESC LIMIT 200""")]

    def cleanup(self):
        now = self.clock()
        with self._db() as db:
            expired = [row[0] for row in db.execute("SELECT id FROM jobs WHERE expires<=?", (now,))]
            for job in expired:
                directory = (self.jobs_dir / job).resolve()
                if directory.parent == self.jobs_dir.resolve() and directory.is_dir():
                    shutil.rmtree(directory)
            db.execute("DELETE FROM email_tokens WHERE created<?", (now - 7 * DAY,))
            db.execute("DELETE FROM events WHERE created<?", (now - 90 * DAY,))
            self._clean_test_mail(db)
