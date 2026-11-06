"""Local, transferable record of OCR review decisions."""
from contextlib import closing
from datetime import datetime, timezone
from io import BytesIO
import json
import os
from pathlib import Path
import re
import sqlite3
from uuid import uuid4
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile
from zlib import error as CompressionError


STORE = Path(os.environ.get("PLANILLA_OCR_STORE", Path(__file__).with_name("aprendizaje-ocr.sqlite3")))
MAX_ARCHIVE = 8 * 1024 * 1024
MEMBER = "correcciones-ocr.json"
LATEST = """
    SELECT id, author, document, page, created, words, changes
    FROM (
        SELECT *, ROW_NUMBER() OVER (
            PARTITION BY author, document, page ORDER BY created DESC, id DESC
        ) AS revision
        FROM reviews
    )
    WHERE revision = 1
    ORDER BY created DESC, id DESC
"""


def connect():
    db = sqlite3.connect(STORE)
    db.row_factory = sqlite3.Row
    with db:
        db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        db.execute("INSERT OR IGNORE INTO settings VALUES ('author', ?)", (uuid4().hex,))
        db.execute("""CREATE TABLE IF NOT EXISTS reviews (
            id TEXT PRIMARY KEY, author TEXT NOT NULL, document TEXT NOT NULL,
            page INTEGER NOT NULL, created TEXT NOT NULL, words INTEGER NOT NULL,
            changes TEXT NOT NULL
        )""")
    return db


def entries(db, query=LATEST, parameters=()):
    return [
        {**dict(row), "changes": json.loads(row["changes"])}
        for row in db.execute(query, parameters)
    ]


def author(db):
    return db.execute("SELECT value FROM settings WHERE key = 'author'").fetchone()[0]


def store_event(db, event):
    return db.execute("INSERT OR IGNORE INTO reviews VALUES (?, ?, ?, ?, ?, ?, ?)", (
        event["id"], event["author"], event["document"], event["page"], event["created"],
        event["words"], json.dumps(event["changes"], ensure_ascii=False),
    )).rowcount


def totals(reviews):
    return {
        "corrections": sum(change["original"] != change["corrected"]
                           for row in reviews for change in row["changes"]),
        "reviews": len(reviews),
    }


def status():
    with closing(connect()) as db:
        return totals(entries(db))


def page_review(document, page):
    with closing(connect()) as db:
        active = author(db)
        reviews = entries(db, """SELECT * FROM reviews WHERE document = ? AND page = ?
            ORDER BY (author = ?) DESC, created DESC, id DESC LIMIT 1""", (document, page, active))
        latest = entries(db)
    examples = [change for row in latest for change in row["changes"]]
    return (reviews[0] if reviews else None), examples


def save(document, page, words, changes):
    with closing(connect()) as db, db:
        event = dict(id=uuid4().hex, author=author(db), document=document, page=page,
                     created=datetime.now(timezone.utc).isoformat(timespec="microseconds"),
                     words=words, changes=changes)
        last = db.execute("""SELECT words, changes FROM reviews
            WHERE author = ? AND document = ? AND page = ?
            ORDER BY created DESC, id DESC LIMIT 1""", (event["author"], document, page)).fetchone()
        if not last or last["words"] != words or json.loads(last["changes"]) != changes:
            store_event(db, event)
        return totals(entries(db))


def export_zip():
    with closing(connect()) as db:
        reviews = entries(db, "SELECT * FROM reviews ORDER BY created, id")
    raw = json.dumps({"format": "planilla-ocr-review", "version": 1, "reviews": reviews},
                     ensure_ascii=False, indent=2).encode("utf-8")
    if len(raw) > MAX_ARCHIVE:
        raise ValueError("El historial supera el tamaño máximo del ZIP de aprendizaje.")
    result = BytesIO()
    with ZipFile(result, "w", ZIP_DEFLATED) as archive:
        archive.writestr(MEMBER, raw)
    return result.getvalue()


def validate_event(row):
    if not isinstance(row, dict):
        raise ValueError
    for key, size in (("id", 32), ("author", 32), ("document", 64)):
        value = row.get(key)
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{%d}" % size, value):
            raise ValueError
    if type(row.get("page")) is not int or not 0 <= row["page"] < 50:
        raise ValueError
    if type(row.get("words")) is not int or not 0 <= row["words"] <= 6000:
        raise ValueError
    stamp = row.get("created")
    if not isinstance(stamp, str) or not re.fullmatch(
        r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}\+00:00", stamp
    ):
        raise ValueError
    datetime.fromisoformat(stamp)
    changes = row.get("changes")
    if not isinstance(changes, list) or len(changes) > row["words"]:
        raise ValueError
    seen = set()
    clean = []
    for change in changes:
        if not isinstance(change, dict):
            raise ValueError
        key = change.get("id")
        original = change.get("original")
        corrected = change.get("corrected")
        if (not isinstance(key, str) or not re.fullmatch(r"\d{1,4}", key)
                or key != str(int(key)) or int(key) >= row["words"] or key in seen
                or not isinstance(original, str) or not 0 < len(original) <= 256
                or not isinstance(corrected, str) or len(corrected) > 256):
            raise ValueError
        if not all(ch.isprintable() for ch in original + corrected):
            raise ValueError
        seen.add(key)
        clean.append({"id": key, "original": original, "corrected": corrected})
    return {**row, "changes": clean}


def import_zip(data):
    try:
        with ZipFile(BytesIO(data)) as archive:
            members = archive.infolist()
            if len(members) != 1 or members[0].filename != MEMBER or members[0].file_size > MAX_ARCHIVE:
                raise ValueError
            with archive.open(members[0]) as member:
                raw = member.read(MAX_ARCHIVE + 1)
        if len(raw) > MAX_ARCHIVE:
            raise ValueError
        payload = json.loads(raw)
        if (not isinstance(payload, dict) or payload.get("format") != "planilla-ocr-review"
                or payload.get("version") != 1 or not isinstance(payload.get("reviews"), list)):
            raise ValueError
        reviews = [validate_event(row) for row in payload["reviews"]]
    except (BadZipFile, CompressionError, EOFError, KeyError, OSError, RuntimeError,
            NotImplementedError, ValueError, RecursionError) as error:
        raise ValueError("El ZIP no contiene un historial de aprendizaje válido de Planilla.") from error
    with closing(connect()) as db, db:
        imported = sum(store_event(db, row) for row in reviews)
        return {**totals(entries(db)), "imported": imported}
