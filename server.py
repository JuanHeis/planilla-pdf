"""Local HTTP interface for the PDF form editor."""
import argparse
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import secrets
import time
from urllib.parse import urlsplit
import webbrowser

import pymupdf

import ocr_review
import ocr_review_store
from pdf_engine import detect_pdf, export_pdf, render_page

STATIC = Path(__file__).with_name("static")
MAX_PDF = 20 * 1024 * 1024
MAX_JSON = 512 * 1024
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


def expire_documents():
    now = time.monotonic()
    for key in list(documents):
        if now - documents[key][0] > DOCUMENT_TTL:
            del documents[key]


class EditorHandler(BaseHTTPRequestHandler):
    timeout = 8

    def handle(self):
        try:
            super().handle()
        except ConnectionError:
            pass

    def respond(self, status, data, content_type, **headers):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; img-src 'self' blob: data:; "
                         "style-src 'self' 'unsafe-inline'; frame-ancestors 'none'")
        for key, value in headers.items():
            self.send_header(key.replace("_", "-"), value)
        self.end_headers()
        self.wfile.write(data)

    def json_response(self, status, payload):
        self.respond(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                     "application/json; charset=utf-8")

    def read_body(self, limit):
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            size = 0
        if not 0 < size <= limit:
            raise ValueError(f"El archivo o la solicitud está vacío o supera el límite de {limit // 1024} KB.")
        return self.rfile.read(size)

    def check_local_request(self):
        port = self.server.server_port
        host = self.headers.get("Host")
        if host not in {f"127.0.0.1:{port}", f"localhost:{port}"}:
            raise PermissionError
        origin = self.headers.get("Origin")
        if origin and origin != f"http://{host}":
            raise PermissionError
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            raise PermissionError

    def get_document(self, key):
        return self.get_session(key)[0]

    def get_session(self, key):
        if not isinstance(key, str) or key not in documents:
            raise LookupError("La sesión del PDF venció. Volvé a abrir el archivo.")
        _, data, cache = documents[key]
        documents[key] = (time.monotonic(), data, cache)
        documents.move_to_end(key)
        return data, cache

    def read_json(self, limit=MAX_JSON):
        if self.headers.get_content_type() != "application/json":
            raise ValueError("Formato de solicitud inválido.")
        try:
            body = json.loads(self.read_body(limit))
        except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
            raise ValueError("Formato de solicitud inválido.") from error
        if not isinstance(body, dict):
            raise ValueError("Formato de solicitud inválido.")
        return body

    def dispatch(self):
        self.check_local_request()
        expire_documents()
        path = urlsplit(self.path).path
        if self.command == "GET":
            if path == "/demo.pdf":
                return self.respond(200, demo_pdf(), "application/pdf")
            if path == "/api/ocr/learning":
                return self.json_response(200, ocr_review_store.status())
            if path == "/api/ocr/learning/export":
                return self.respond(200, ocr_review_store.export_zip(), "application/zip",
                                    Content_Disposition='attachment; filename="aprendizaje-planilla.zip"')
            if path.startswith("/api/ocr/"):
                parts = path.split("/")
                if len(parts) != 5 or not parts[4].isascii() or not parts[4].isdigit() or len(parts[4]) > 2:
                    raise LookupError("Página inválida.")
                data, cache = self.get_session(parts[3])
                return self.json_response(200, ocr_review.reviewed_page(data, int(parts[4]), cache))
            if path.startswith("/api/page/"):
                parts = path.split("/")
                if len(parts) != 5 or not parts[4].isdigit() or len(parts[4]) > 4:
                    raise LookupError("Página inválida.")
                return self.respond(200, render_page(self.get_document(parts[3]), int(parts[4])),
                                    "image/png")
            if path == "/":
                path = "/static/index.html"
            if not path.startswith("/static/"):
                raise LookupError("No encontramos ese recurso.")
            candidate = STATIC / path.removeprefix("/static/")
            mime = {".html": "text/html", ".js": "text/javascript", ".css": "text/css",
                    ".svg": "image/svg+xml"}.get(candidate.suffix)
            if mime and candidate.parent == STATIC and candidate.is_file():
                return self.respond(200, candidate.read_bytes(), f"{mime}; charset=utf-8")
        elif path in {"/api/detect", "/api/ocr"}:
            if self.headers.get_content_type() != "application/pdf":
                raise ValueError("Seleccioná un archivo PDF.")
            data = self.read_body(MAX_PDF)
            if b"%PDF-" not in data[:1024]:
                raise ValueError("El archivo no parece ser un PDF.")
            result = detect_pdf(data) if path == "/api/detect" else ocr_review.describe(data)
            key = secrets.token_urlsafe(24)
            # shortcut: sessions live in one process for local use. Use expiring object storage before hosting.
            if len(documents) >= 4:
                documents.popitem(last=False)
            documents[key] = (time.monotonic(), data, {})
            return self.json_response(200, {**result, "documentId": key})
        elif path == "/api/ocr/review":
            body = self.read_json(2 * 1024 * 1024)
            index = body.get("page")
            if type(index) is not int:
                raise ValueError("Página inválida.")
            data, cache = self.get_session(body.get("documentId"))
            return self.json_response(200, ocr_review.save_review(data, index, cache, body.get("corrections")))
        elif path == "/api/ocr/learning/import":
            if self.headers.get_content_type() not in {"application/zip", "application/x-zip-compressed"}:
                raise ValueError("Seleccioná un ZIP de aprendizaje.")
            result = ocr_review_store.import_zip(self.read_body(ocr_review_store.MAX_ARCHIVE))
            return self.json_response(200, result)
        elif path == "/api/export":
            body = self.read_json()
            if not isinstance(body.get("fields"), list):
                raise ValueError("La lista de campos es inválida.")
            output = export_pdf(self.get_document(body.get("documentId")), body["fields"])
            return self.respond(200, output, "application/pdf",
                                Content_Disposition='attachment; filename="formulario-rellenable.pdf"')
        raise LookupError("No encontramos ese recurso.")

    def handle_request(self):
        try:
            self.dispatch()
        except PermissionError:
            self.json_response(403, {"error": "La aplicación solo admite solicitudes locales."})
        except LookupError as error:
            self.json_response(404, {"error": str(error)})
        except ValueError as error:
            self.json_response(400, {"error": str(error)})
        except ConnectionError:
            raise
        except Exception as error:
            self.log_error("Processing failed (%s)", type(error).__name__)
            self.json_response(500, {"error": "No pudimos procesar el PDF."})

    do_GET = do_POST = handle_request

    def log_request(self, code="-", size="-"):
        # Keep the temporary document ID out of request logs.
        pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Editor local de formularios PDF")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true", help="Abrir el navegador")
    options = parser.parse_args()
    with HTTPServer(("127.0.0.1", options.port), EditorHandler) as server:
        url = f"http://127.0.0.1:{server.server_port}"
        print(f"Editor disponible en {url}", flush=True)
        if options.open:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
