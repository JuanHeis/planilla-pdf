"""Local editor and hosted, session-protected Planilla web application."""
import argparse
from collections import OrderedDict, deque
from datetime import timedelta
from functools import wraps
from hashlib import sha256
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import threading
import time
from urllib.parse import urlsplit
import webbrowser

from flask import Flask, g, jsonify, redirect, request, send_file
from werkzeug.exceptions import HTTPException
from werkzeug.serving import make_server

import pymupdf

import ocr_review
import ocr_review_store
from pdf_engine import detect_pdf, export_pdf, render_page

ROOT = Path(__file__).resolve().parent
MAX_PDF = 20 * 1024 * 1024
MAX_JSON = 2 * 1024 * 1024
DOCUMENT_TTL = 30 * 60
documents = OrderedDict()


def demo_pdf():
    with pymupdf.open() as document:
        page = document.new_page(width=595, height=740)
        page.insert_text((48, 67), "Solicitud de atención", fontsize=26)
        page.insert_text((48, 96), "FORMULARIO DE EJEMPLO", fontsize=10)
        page.draw_line((48, 114), (547, 114), color=(0.65, 0.65, 0.65))
        for x, y, label, width in (
            (48, 151, "Nombre y apellido", 499),
            (48, 226, "Documento", 235),
            (312, 226, "Teléfono", 235),
            (48, 301, "Correo electrónico", 499),
            (48, 376, "Domicilio", 499),
        ):
            page.insert_text((x, y), label, fontsize=11)
            page.draw_rect(pymupdf.Rect(x, y + 8, x + width, y + 37), width=0.8)
        page.insert_text((48, 461), "Medio de contacto preferido", fontsize=11)
        for x, label in ((48, "Correo electrónico"), (248, "Teléfono")):
            page.draw_rect(pymupdf.Rect(x, 477, x + 13, 490), width=0.8)
            page.insert_text((x + 20, 487), label, fontsize=10)
        page.insert_text((48, 537), "Observaciones", fontsize=11)
        page.draw_rect(pymupdf.Rect(48, 545, 547, 660), width=0.8)
        return document.tobytes()


def read_json():
    if request.mimetype != "application/json" or (request.content_length or 0) > MAX_JSON:
        raise ValueError("La solicitud tiene un formato o tamaño inválido.")
    value = request.get_json()
    if not isinstance(value, dict):
        raise ValueError("La solicitud debe ser un objeto.")
    return value


def create_app(*, mode=None, commerce_service=None):
    app = Flask(__name__, static_folder=str(ROOT / "static"))
    app.config["MAX_CONTENT_LENGTH"] = MAX_PDF
    chosen_mode = mode or os.environ.get("PLANILLA_MODE", "local")
    if chosen_mode not in {"local", "web"}:
        raise RuntimeError("PLANILLA_MODE debe ser local o web.")
    web = chosen_mode == "web"
    public = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
    parsed = urlsplit(public)
    if web and (parsed.scheme not in {"https", "http"} or not parsed.netloc or parsed.path
                or parsed.query or parsed.fragment or parsed.username or parsed.password):
        raise RuntimeError("Configurá PUBLIC_BASE_URL con la URL pública de Planilla.")
    if web and os.environ.get("APP_ENV", "").lower() == "production" and parsed.scheme != "https":
        raise RuntimeError("La versión pública requiere una PUBLIC_BASE_URL con HTTPS.")
    data_dir = Path(os.environ.get("PLANILLA_DATA_DIR", ROOT / "data"))
    shop = commerce_service
    if web and shop is None:
        from commerce import Commerce
        shop = Commerce(data_dir=data_dir)
    app.extensions["commerce"] = shop
    cookie_name = "planilla_session"
    uploads = {}
    last_cleanup = [0.0]
    source_base = "https://github.com/JuanHeis/planilla-pdf"
    revision = os.environ.get("RAILWAY_GIT_COMMIT_SHA", "")
    source_url = os.environ.get("SOURCE_URL") or (
        f"{source_base}/tree/{revision}" if re.fullmatch(r"[0-9a-f]{40}", revision) else source_base
    )

    def emit(event, job=None):
        if web and hasattr(shop, "event"):
            shop.event(g.sid, event, job=job)

    def job_dir(job):
        if not isinstance(job, str) or not re.fullmatch(r"[0-9a-f]{32}", job):
            raise LookupError("El enlace al documento es inválido.")
        return data_dir / "jobs" / job

    def metadata_path(job):
        return job_dir(job) / "metadata.json"

    def save_metadata(job, value):
        target = metadata_path(job)
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        temporary.replace(target)

    def prepare_hosted(job, data, fields):
        meta = json.loads(metadata_path(job).read_text(encoding="utf-8"))
        fingerprint = sha256(json.dumps(fields, sort_keys=True, ensure_ascii=False,
                                        separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        final = job_dir(job) / "final.pdf"
        # Reuse the prepared bytes: PDF serialization generates a fresh document ID.
        if meta.get("preparedFieldsDigest") == fingerprint and final.is_file():
            output = final.read_bytes()
        else:
            output = export_pdf(data, fields)
        status = shop.prepare(g.sid, job, output,
                              original=not fields and bool(meta.get("existingFields")))
        corrected = meta.get("correctionMarked", False)
        if not corrected and meta.get("initialFieldsDigest") and meta["initialFieldsDigest"] != fingerprint:
            emit("corrected", job)
            corrected = True
        save_metadata(job, {**meta, "fields": fields, "preparedFieldsDigest": fingerprint,
                            "correctionMarked": corrected})
        return jsonify(status)

    def session_data(job):
        if web:
            shop.authorize(g.sid, job)
            folder = job_dir(job)
            if not (folder / "original.pdf").is_file():
                raise LookupError("El documento venció. Podés volver a subirlo.")
            return (folder / "original.pdf").read_bytes(), {}
        now = time.monotonic()
        for key in list(documents):
            if now - documents[key][0] > DOCUMENT_TTL:
                del documents[key]
        if not isinstance(job, str) or job not in documents:
            raise LookupError("La sesión del PDF venció. Volvé a abrir el archivo.")
        _, data, cache = documents[job]
        documents[job] = (now, data, cache)
        documents.move_to_end(job)
        return data, cache

    def hosted_only(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            if not web:
                raise LookupError("Esta función está disponible en la versión web.")
            return fn(*args, **kwargs)
        return wrapped

    def local_only():
        if web:
            raise LookupError("La revisión de aprendizaje se usa en la aplicación local.")

    def admin_only(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            expected = os.environ.get("ADMIN_TOKEN", "")
            received = request.headers.get("Authorization", "")
            if not expected or not hmac.compare_digest(received, "Bearer " + expected):
                return jsonify(error="Acceso administrativo requerido."), 401
            return fn(*args, **kwargs)
        return wrapped

    @app.before_request
    def security():
        if request.path == "/health":
            return None
        host = request.host
        origin = request.headers.get("Origin")
        if web:
            if host != parsed.netloc or (origin and origin != public):
                return jsonify(error="La solicitud no pertenece a este sitio."), 403
            if request.path == "/api/webhooks/mercadopago":
                return None
            g.sid = shop.ensure_session(request.cookies.get(cookie_name))
            now = time.monotonic()
            if now - last_cleanup[0] > 300 and hasattr(shop, "cleanup"):
                shop.cleanup()
                last_cleanup[0] = now
            if request.path.startswith("/static/ocr") or request.path.startswith("/api/ocr"):
                return jsonify(error="La revisión de aprendizaje se usa en la aplicación local."), 404
        else:
            if not (host.startswith("localhost:") or host.startswith("127.0.0.1:")):
                return jsonify(error="La aplicación solo admite solicitudes locales."), 403
            if origin and origin != "http://" + host:
                return jsonify(error="La solicitud no pertenece a este sitio."), 403
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            if request.headers.get("Sec-Fetch-Site") == "cross-site":
                return jsonify(error="Abrí Planilla directamente para continuar."), 403

    @app.after_request
    def headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' blob: data:; "
            "style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; "
            "base-uri 'self'; form-action 'self'"
        )
        if web and hasattr(g, "sid"):
            response.set_cookie(cookie_name, g.sid, secure=parsed.scheme == "https",
                                httponly=True, samesite="Lax", max_age=int(timedelta(days=180).total_seconds()))
        return response

    @app.errorhandler(Exception)
    def handle_error(error):
        if web and hasattr(g, "sid"):
            event = "detection_failed" if request.path == "/api/detect" else (
                "prepare_failed" if request.path in {"/api/prepare", "/api/export"} or request.path.endswith("/prepare") else None)
            if event:
                emit(event)
        if hasattr(error, "code") and hasattr(error, "status"):
            return jsonify(error=str(error), code=error.code), error.status
        if isinstance(error, HTTPException):
            messages = {400: "Revisá los datos enviados.", 404: "No encontramos ese recurso.",
                        413: "El archivo supera el límite de 20 MB."}
            return jsonify(error=messages.get(error.code, "No pudimos completar la solicitud.")), error.code
        if isinstance(error, (ValueError, LookupError)):
            return jsonify(error=str(error)), 400 if isinstance(error, ValueError) else 404
        app.logger.error("Request failed (%s)", type(error).__name__)
        return jsonify(error="No pudimos completar la operación. Podés reintentar."), 500

    @app.get("/health")
    def health():
        return jsonify(ok=True)

    @app.get("/")
    def index():
        return app.send_static_file("index.html")

    @app.get("/source")
    def source():
        return redirect(source_url, code=302)

    @app.get("/license")
    def license_text():
        return send_file(ROOT / "LICENSE", mimetype="text/plain")

    @app.get("/api/config")
    def config():
        return jsonify(commercial=web, price=2990, currency="ARS", maxPages=5, freeMaxPages=2,
                       sourceUrl="/source", supportEmail=os.environ.get("SUPPORT_EMAIL", ""),
                       emailConfigured=bool(os.environ.get("RESEND_API_KEY") or
                                            (os.environ.get("SMTP_USERNAME") and os.environ.get("SMTP_PASSWORD"))))

    @app.get("/demo.pdf")
    def demo():
        return app.response_class(demo_pdf(), mimetype="application/pdf")

    @app.post("/api/detect")
    @app.post("/api/ocr")
    def detect():
        if request.path == "/api/ocr":
            local_only()
        if request.mimetype != "application/pdf":
            raise ValueError("Seleccioná un archivo PDF.")
        data = request.get_data()
        if not data or b"%PDF-" not in data[:1024]:
            raise ValueError("El archivo no parece ser un PDF.")
        if web:
            # Reject over-limit documents before geometric analysis and OCR.
            try:
                document = pymupdf.open(stream=data, filetype="pdf")
            except (RuntimeError, ValueError) as error:
                raise ValueError("No se pudo abrir el PDF. Revisá que el archivo esté completo. No se consumió tu gratuito.") from error
            with document:
                if document.page_count > 5:
                    raise ValueError("Por ahora convertimos PDFs de hasta 5 páginas. No se consumió tu gratuito.")
            now = time.monotonic()
            recent = uploads.setdefault(g.sid, deque())
            while recent and now - recent[0] > 3600:
                recent.popleft()
            if len(recent) >= 12:
                return jsonify(error="Llegaste al límite temporal de archivos. Volvé a intentarlo más tarde."), 429
            recent.append(now)
            if len(uploads) > 10000:
                for session in list(uploads):
                    if not uploads[session] or now - uploads[session][-1] > 3600:
                        del uploads[session]
        result = detect_pdf(data) if request.path == "/api/detect" else ocr_review.describe(data)
        if web and len(result["pages"]) > 5:
            raise ValueError("Por ahora convertimos PDFs de hasta 5 páginas. No se consumió tu gratuito.")
        key = secrets.token_hex(16)
        if web:
            folder = job_dir(key)
            folder.mkdir(parents=True, exist_ok=False)
            (folder / "original.pdf").write_bytes(data)
            initial_digest = sha256(json.dumps(result["fields"], sort_keys=True, ensure_ascii=False,
                                               separators=(",", ":")).encode()).hexdigest()
            save_metadata(key, {**result, "documentId": key, "initialFieldsDigest": initial_digest})
            try:
                shop.register_job(g.sid, key, len(result["pages"]), len(result.get("existingFields", [])))
            except Exception:
                (folder / "original.pdf").unlink(missing_ok=True)
                metadata_path(key).unlink(missing_ok=True)
                folder.rmdir()
                raise
            emit("detected", key)
        else:
            if len(documents) >= 4:
                documents.popitem(last=False)
            documents[key] = (time.monotonic(), data, {})
        return jsonify({**result, "documentId": key})

    @app.get("/api/page/<job>/<int:index>")
    def page(job, index):
        data, _ = session_data(job)
        return app.response_class(render_page(data, index), mimetype="image/png")

    @app.get("/api/jobs/<job>/metadata")
    @hosted_only
    def metadata(job):
        session_data(job)
        return jsonify(json.loads(metadata_path(job).read_text(encoding="utf-8")))

    @app.get("/api/jobs")
    @hosted_only
    def saved_jobs():
        return jsonify(jobs=shop.jobs(g.sid))

    @app.post("/api/export")
    @app.post("/api/prepare")
    def prepare():
        body = read_json()
        fields = body.get("fields")
        if not isinstance(fields, list):
            raise ValueError("La lista de campos es inválida.")
        job = body.get("documentId")
        data, _ = session_data(job)
        if web:
            return prepare_hosted(job, data, fields)
        output = export_pdf(data, fields)
        return app.response_class(output, mimetype="application/pdf", headers={
            "Content-Disposition": 'attachment; filename="formulario-rellenable.pdf"'
        })

    @app.post("/api/jobs/<job>/prepare")
    @hosted_only
    def prepare_job(job):
        body = read_json()
        data, _ = session_data(job)
        fields = body.get("fields")
        if not isinstance(fields, list):
            raise ValueError("La lista de campos es inválida.")
        return prepare_hosted(job, data, fields)

    @app.get("/api/jobs/<job>/original")
    @hosted_only
    def original(job):
        data, _ = session_data(job)
        meta = json.loads(metadata_path(job).read_text(encoding="utf-8"))
        if not meta.get("existingFields"):
            raise ValueError("Este PDF todavía requiere campos rellenables.")
        emit("original_download", job)
        return app.response_class(data, mimetype="application/pdf", headers={
            "Content-Disposition": 'attachment; filename="formulario-original-rellenable.pdf"'
        })

    @app.get("/api/jobs/<job>/status")
    @hosted_only
    def status(job):
        return jsonify(shop.refresh(g.sid, job))

    @app.post("/api/jobs/<job>/email")
    @hosted_only
    def email(job):
        return jsonify(shop.request_email(g.sid, job, read_json().get("email")))

    @app.get("/verify")
    @hosted_only
    def verify():
        try:
            result = shop.verify(request.args.get("token", ""))
        except ValueError:
            return redirect("/?verify_error=expired", code=303)
        g.sid = result["sessionId"]
        return redirect("/?job=" + result["jobId"], code=303)

    @app.post("/api/recovery")
    @hosted_only
    def recovery():
        return jsonify(shop.recover(read_json().get("email")))

    @app.post("/api/jobs/<job>/free")
    @hosted_only
    def free(job):
        return jsonify(shop.claim_free(g.sid, job))

    @app.post("/api/jobs/<job>/checkout")
    @hosted_only
    def checkout(job):
        return jsonify(shop.checkout(g.sid, job))

    @app.get("/api/jobs/<job>/download")
    @hosted_only
    def download(job):
        final = shop.download(g.sid, job)
        return send_file(final, mimetype="application/pdf", as_attachment=True,
                         download_name="formulario-rellenable.pdf")

    @app.post("/api/jobs/<job>/claim")
    @hosted_only
    def claim(job):
        return jsonify(shop.claim(g.sid, job, read_json().get("reason")))

    @app.post("/api/webhooks/mercadopago")
    @hosted_only
    def webhook():
        payload = request.get_json(silent=True) or {}
        return jsonify(shop.process_webhook(request.args, request.headers, payload))

    @app.post("/api/events")
    @hosted_only
    def events():
        event = read_json().get("event")
        if event not in {"page_view", "editor_open"}:
            raise ValueError("Evento inválido.")
        emit(event)
        return jsonify(ok=True)

    @app.get("/api/admin/metrics")
    @hosted_only
    @admin_only
    def metrics():
        return jsonify(shop.metrics())

    @app.get("/api/admin/claims")
    @hosted_only
    @admin_only
    def claims():
        return jsonify(shop.claims())

    @app.post("/api/admin/claims/<claim_id>/refund")
    @hosted_only
    @admin_only
    def refund(claim_id):
        return jsonify(shop.refund(claim_id))

    @app.get("/api/ocr/<job>/<int:index>")
    def ocr_page(job, index):
        local_only()
        data, cache = session_data(job)
        return jsonify(ocr_review.reviewed_page(data, index, cache))

    @app.post("/api/ocr/review")
    def ocr_review_page():
        local_only()
        body = read_json()
        index = body.get("page")
        if type(index) is not int:
            raise ValueError("La página es inválida.")
        data, cache = session_data(body.get("documentId"))
        return jsonify(ocr_review.save_review(data, index, cache, body.get("corrections")))

    @app.get("/api/ocr/learning")
    def ocr_learning_status():
        local_only()
        return jsonify(ocr_review_store.status())

    @app.get("/api/ocr/learning/export")
    def ocr_learning_export():
        local_only()
        return app.response_class(ocr_review_store.export_zip(), mimetype="application/zip", headers={
            "Content-Disposition": 'attachment; filename="aprendizaje-planilla.zip"'
        })

    @app.post("/api/ocr/learning/import")
    def ocr_learning_import():
        local_only()
        if request.mimetype not in {"application/zip", "application/x-zip-compressed"}:
            raise ValueError("Seleccioná un ZIP de aprendizaje.")
        data = request.get_data()
        if len(data) > ocr_review_store.MAX_ARCHIVE:
            raise ValueError("El archivo de aprendizaje es demasiado grande.")
        return jsonify(ocr_review_store.import_zip(data))

    if web and os.environ.get("PLANILLA_BACKGROUND_CLEANUP") == "1":
        def cleanup_loop():
            while True:
                try:
                    shop.cleanup()
                except Exception as error:
                    app.logger.error("Private-file cleanup failed (%s)", type(error).__name__)
                time.sleep(300)

        threading.Thread(target=cleanup_loop, name="planilla-cleanup", daemon=True).start()

    return app


app = create_app()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Planilla · Editor de formularios PDF")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true", help="Abrir el navegador")
    options = parser.parse_args()
    host = "0.0.0.0" if os.environ.get("PLANILLA_MODE") == "web" else "127.0.0.1"
    with make_server(host, options.port, app, threaded=False) as server:
        url = f"http://127.0.0.1:{server.server_port}"
        print(f"Editor disponible en {url}", flush=True)
        if options.open:
            webbrowser.open(url)
        server.serve_forever()
