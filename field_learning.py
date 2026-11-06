"""Conservative learning from explicitly reviewed field geometry, without text."""
from collections import defaultdict
from contextlib import contextmanager
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
import math
import os
from pathlib import Path
import secrets
import sqlite3
import time
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile


MIN_ADJUST_LAYOUTS = 5
MIN_DELETE_LAYOUTS = 8
MAX_BUNDLE = 2 * 1024 * 1024
KINDS = {"text": 0, "checkbox": 1}
SOURCES = {"vector": 0, "raster": 1, "text": 2}
COUNTS = ("approved", "adjusted", "deleted", "ambiguous")


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and abs(value) <= 1e6


def _boxes(fields, pages):
    if not isinstance(fields, list) or len(fields) > 1000:
        raise ValueError("La revisión debe contener hasta 1000 campos.")
    if not isinstance(pages, list) or not 1 <= len(pages) <= 50:
        raise ValueError("La lista de páginas es inválida.")
    for page in pages:
        if not isinstance(page, dict) or not all(_number(page.get(k)) and page[k] > 0 for k in ("width", "height")):
            raise ValueError("El tamaño de página es inválido.")
    result = []
    for field in fields:
        if not isinstance(field, dict):
            raise ValueError("El campo no tiene un formato válido.")
        page = field.get("page")
        if type(page) is not int or not 0 <= page < len(pages):
            raise ValueError("El campo pertenece a una página inválida.")
        if not all(_number(field.get(k)) for k in ("x", "y", "width", "height")):
            raise ValueError("Las coordenadas del campo son inválidas.")
        x, y, w, h = (float(field[k]) for k in ("x", "y", "width", "height"))
        if min(w, h) < 4 or min(x, y) < -0.05 or x + w > pages[page]["width"] + .05 or y + h > pages[page]["height"] + .05:
            raise ValueError("El campo está fuera de la página.")
        result.append((page, x, y, w, h))
    return result


def _area_overlap(a, b):
    if a[0] != b[0]:
        return 0.0
    _, x, y, w, h = a
    _, xx, yy, ww, hh = b
    return max(0, min(x + w, xx + ww) - max(x, xx)) * max(0, min(y + h, yy + hh) - max(y, yy))


def _iou(a, b):
    intersection = _area_overlap(a, b)
    return intersection / (a[3] * a[4] + b[3] * b[4] - intersection) if intersection else 0.0


def _gap_bucket(gap, height):
    return next((i for i, maximum in enumerate((.5, 2, 8)) if gap / height <= maximum), 3)


def _pattern(index, fields, boxes, pages):
    field = fields[index]
    if field.get("type") not in KINDS or field.get("source") not in SOURCES or str(field.get("id", "")).startswith("existing-") or field.get("readOnly"):
        return None
    page, x, y, w, h = boxes[index]
    pw, ph = pages[page]["width"], pages[page]["height"]
    row = [b for j, b in enumerate(boxes) if j != index and b[0] == page and abs((b[2] + b[4] / 2) - (y + h / 2)) < .65 * max(h, b[4])]
    left = [x - b[1] - b[3] for b in row if b[1] + b[3] <= x + .5]
    right = [b[1] - x - w for b in row if b[1] >= x + w - .5]
    column = sum(1 for j, b in enumerate(boxes) if j != index and b[0] == page and abs(b[1] - x) < .75 * h and abs(b[3] - w) < max(w * .15, 4))
    edge = int(x < pw * .02) + 2 * int(y < ph * .02) + 4 * int(x + w > pw * .98) + 8 * int(y + h > ph * .98)
    key = [KINDS[field["type"]], SOURCES[field["source"]], round(w / pw / .04), round(h / ph / .005),
           min(4, round(w / h / 3)), min(3, len(row)), min(3, column),
           _gap_bucket(min(left), h) if left else 4, _gap_bucket(min(right), h) if right else 4, edge]
    return _json(key)


def _layout(boxes, pages):
    # A different filename, PDF metadata, upload or job is not another layout.
    normalized = sorted([p, round(x / pages[p]["width"], 3), round(y / pages[p]["height"], 3),
                         round(w / pages[p]["width"], 3), round(h / pages[p]["height"], 3)] for p, x, y, w, h in boxes)
    return sha256(_json(normalized).encode()).hexdigest()


def _empty():
    return {**dict.fromkeys(COUNTS, 0), "sum": [0.] * 4, "min": [0.] * 4, "max": [0.] * 4, "support": 0}


def _vote(group):
    actions = {entry["action"] for entry in group}
    if len(actions) != 1:
        return "ambiguous", None
    action = next(iter(actions))
    if action != "adjusted":
        return action, None
    vectors = [entry["delta"] for entry in group]
    if any(max(v[i] for v in vectors) - min(v[i] for v in vectors) > .06 for i in range(4)):
        return "ambiguous", None
    return action, [sum(v[i] for v in vectors) / len(vectors) for i in range(4)]


def _add_vote(stats, action, delta):
    stats[action] += 1
    stats["support"] += 1
    if delta is not None:
        previous = stats["adjusted"] - 1
        for i in range(4):
            stats["sum"][i] += delta[i]
            stats["min"][i] = min(stats["min"][i], delta[i]) if previous else delta[i]
            stats["max"][i] = max(stats["max"][i], delta[i]) if previous else delta[i]


class FieldLearner:
    def __init__(self, data_dir=None, clock=None):
        directory = Path(data_dir or os.environ.get("PLANILLA_DATA_DIR", "data")).resolve()
        directory.mkdir(parents=True, exist_ok=True)
        self.database = directory / "field-learning.sqlite3"
        self.clock = clock or time.time
        self._cache = None
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS settings (name TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS reviews (
                    job_key TEXT PRIMARY KEY, layout_key TEXT NOT NULL, created REAL NOT NULL,
                    observations TEXT NOT NULL, added INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS imported_sources (id TEXT PRIMARY KEY, patterns TEXT NOT NULL);
            """)
            db.execute("INSERT OR IGNORE INTO settings VALUES('origin',?)", (secrets.token_hex(16),))
            db.execute("INSERT OR IGNORE INTO settings VALUES('revision','0')")

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.database, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def record_review(self, job_id, proposed, reviewed, pages, document_fingerprint=None):
        """Caller must obtain explicit confirmation; the method never infers it."""
        if not isinstance(job_id, str) or not 1 <= len(job_id) <= 160:
            raise ValueError("El trabajo de la revisión es inválido.")
        if document_fingerprint is not None and (not isinstance(document_fingerprint, str) or len(document_fingerprint) != 64 or any(c not in "0123456789abcdefABCDEF" for c in document_fingerprint)):
            raise ValueError("La huella del documento es inválida.")
        before, after = _boxes(proposed, pages), _boxes(reviewed, pages)
        ids = defaultdict(list)
        for i, field in enumerate(reviewed):
            if isinstance(field.get("id"), str):
                ids[field["id"]].append(i)
        used, observations = set(), []
        summary = dict.fromkeys(("approved", "moved", "resized", "deleted", "added"), 0)
        for i, field in enumerate(proposed):
            pattern = _pattern(i, proposed, before, pages)
            if pattern is None:
                continue
            exact = ids.get(field.get("id"), []) if isinstance(field.get("id"), str) else []
            matches = [j for j in exact if j not in used and reviewed[j].get("type") == field.get("type") and after[j][0] == before[i][0]]
            if len(matches) != 1:
                candidates = sorted([(_iou(before[i], box), j) for j, box in enumerate(after)
                                     if j not in used and reviewed[j].get("type") == field.get("type")], reverse=True)
                matches = [candidates[0][1]] if candidates and candidates[0][0] >= .7 and (len(candidates) == 1 or candidates[0][0] - candidates[1][0] > .1) else []
            if not matches:
                summary["deleted"] += 1
                observations.append({"pattern": pattern, "action": "deleted"})
                continue
            j = matches[0]
            used.add(j)
            _, x, y, w, h = before[i]
            _, xx, yy, ww, hh = after[j]
            delta = [(xx - x) / w, (yy - y) / h, (ww - w) / w, (hh - h) / h]
            if max(abs(v) for v in delta) < .002:
                summary["approved"] += 1
                observations.append({"pattern": pattern, "action": "approved"})
            else:
                summary["moved"] += int(abs(xx - x) > .1 or abs(yy - y) > .1)
                summary["resized"] += int(abs(ww - w) > .1 or abs(hh - h) > .1)
                action = "adjusted" if all(abs(v) <= .2 for v in delta) else "ambiguous"
                observations.append({"pattern": pattern, "action": action, **({"delta": [round(v, 6) for v in delta]} if action == "adjusted" else {})})
        summary["added"] = sum(1 for j, field in enumerate(reviewed) if j not in used and not str(field.get("id", "")).startswith("existing-"))
        job_key = sha256(job_id.encode()).hexdigest()
        with self._db() as db:
            db.execute("INSERT INTO reviews VALUES(?,?,?,?,?) ON CONFLICT(job_key) DO UPDATE SET layout_key=excluded.layout_key,created=excluded.created,observations=excluded.observations,added=excluded.added",
                       (job_key, _layout(before, pages), self.clock(), _json(observations), summary["added"]))
            db.execute("UPDATE settings SET value=CAST(value AS INTEGER)+1 WHERE name='revision'")
        return {"recorded": True, "changes": summary, **self.stats()}

    def forget_review(self, job_id):
        with self._db() as db:
            db.execute("DELETE FROM reviews WHERE job_key=?", (sha256(job_id.encode()).hexdigest(),))
            db.execute("UPDATE settings SET value=CAST(value AS INTEGER)+1 WHERE name='revision'")

    def _sources(self, db):
        by_layout = defaultdict(list)
        for row in db.execute("SELECT layout_key,observations FROM reviews"):
            for entry in json.loads(row["observations"]):
                by_layout[(entry["pattern"], row["layout_key"])].append(entry)
        local = {}
        for (pattern, _), group in by_layout.items():
            action, delta = _vote(group)
            _add_vote(local.setdefault(pattern, _empty()), action, delta)
        origin = db.execute("SELECT value FROM settings WHERE name='origin'").fetchone()[0]
        sources = {row["id"]: json.loads(row["patterns"]) for row in db.execute("SELECT * FROM imported_sources")}
        sources[origin] = local
        return origin, sources

    def _model(self):
        with self._db() as db:
            revision = db.execute("SELECT value FROM settings WHERE name='revision'").fetchone()[0]
            if self._cache and self._cache[0] == revision:
                return self._cache[1]
            _, sources = self._sources(db)
        model = {}
        for patterns in sources.values():
            for pattern, stats in patterns.items():
                combined = model.setdefault(pattern, _empty())
                previous = combined["adjusted"]
                for key in COUNTS:
                    combined[key] += stats[key]
                # Exported aggregates cannot prove disjoint layouts across installations.
                combined["support"] = max(combined["support"], stats["support"])
                for i in range(4):
                    combined["sum"][i] += stats["sum"][i]
                    if stats["adjusted"]:
                        combined["min"][i] = min(combined["min"][i], stats["min"][i]) if previous else stats["min"][i]
                        combined["max"][i] = max(combined["max"][i], stats["max"][i]) if previous else stats["max"][i]
        self._cache = (revision, model)
        return model

    @staticmethod
    def _eligible(stats):
        if stats["ambiguous"]:
            return None
        if stats["adjusted"] and not stats["approved"] and not stats["deleted"] and stats["support"] >= MIN_ADJUST_LAYOUTS:
            if all(stats["max"][i] - stats["min"][i] <= .06 for i in range(4)):
                return "adjusted"
        if stats["deleted"] and not stats["approved"] and not stats["adjusted"] and stats["support"] >= MIN_DELETE_LAYOUTS:
            return "suppressed"
        return None

    def suggest(self, fields, pages, auto_apply=True, existing_fields=()):
        boxes = _boxes(fields, pages)
        existing = _boxes(list(existing_fields), pages)
        model, changes, result = self._model(), [], deepcopy(fields)
        current_boxes = list(boxes)
        removed = set()
        for i, field in enumerate(fields):
            pattern = _pattern(i, fields, boxes, pages)
            stats = model.get(pattern)
            action = self._eligible(stats) if stats else None
            if action is None:
                continue
            change = {"id": field.get("id"), "page": field["page"], "action": action,
                      "support": stats["support"], "applied": False}
            if action == "suppressed":
                # Protect high-confidence candidates even if one coarse pattern is deleted often.
                confidence = field.get("confidence", 1)
                if not _number(confidence) or confidence >= .8:
                    continue
                if auto_apply:
                    removed.add(i)
                    change["applied"] = True
            else:
                page, x, y, w, h = boxes[i]
                delta = [v / stats["adjusted"] for v in stats["sum"]]
                dx, dy, dw, dh = delta[0] * w, delta[1] * h, delta[2] * w, delta[3] * h
                proposed = (page, x + dx, y + dy, w + dw, h + dh)
                safe = (abs(dx) <= min(6, w * .2) and abs(dy) <= min(6, h * .2)
                        and abs(dw) <= min(12, w * .2) and abs(dh) <= min(6, h * .2)
                        and min(proposed[3:]) >= 4 and min(proposed[1:3]) >= 0
                        and proposed[1] + proposed[3] <= pages[page]["width"]
                        and proposed[2] + proposed[4] <= pages[page]["height"])
                obstacles = [current_boxes[j] for j in range(len(boxes)) if j != i and j not in removed] + existing
                if safe:
                    safe = not any(_area_overlap(proposed, other) > max(_area_overlap(boxes[i], other), 1.) for other in obstacles)
                if not safe:
                    continue
                geometry = dict(zip(("x", "y", "width", "height"), (round(v, 2) for v in proposed[1:])))
                change["geometry"] = geometry
                if auto_apply:
                    result[i].update(geometry)
                    current_boxes[i] = proposed
                    change["applied"] = True
            changes.append(change)
        return {"fields": [f for i, f in enumerate(result) if i not in removed], "changes": changes,
                "modelVersion": sha256(_json(model).encode()).hexdigest()[:12],
                "mode": "conservative" if auto_apply else "shadow", "stats": self.stats()}

    def stats(self):
        model = self._model()
        with self._db() as db:
            reviews = db.execute("SELECT COUNT(*),COUNT(DISTINCT layout_key),COALESCE(SUM(added),0) FROM reviews").fetchone()
            outcomes = defaultdict(int)
            for row in db.execute("SELECT observations FROM reviews"):
                for item in json.loads(row[0]):
                    outcomes[item["action"]] += 1
            imported = db.execute("SELECT COUNT(*) FROM imported_sources").fetchone()[0]
        return {"reviews": reviews[0], "layouts": reviews[1], "addedFieldsReviewed": reviews[2],
                "reviewedProposedFields": sum(outcomes.values()), "approvedFieldsReviewed": outcomes["approved"],
                "correctedProposedFields": outcomes["adjusted"] + outcomes["ambiguous"] + outcomes["deleted"],
                "deletedFieldsReviewed": outcomes["deleted"],
                "patterns": len(model), "eligibleAdjustments": sum(self._eligible(v) == "adjusted" for v in model.values()),
                "eligibleSuppressions": sum(self._eligible(v) == "suppressed" for v in model.values()),
                "importedSources": imported, "modelVersion": sha256(_json(model).encode()).hexdigest()[:12]}

    def export_bundle(self):
        with self._db() as db:
            _, sources = self._sources(db)
        payload = _json({"format": "planilla-field-learning", "version": 1, "sources": sources}).encode()
        if len(payload) > MAX_BUNDLE:
            raise ValueError("El aprendizaje supera el tamaño de exportación del piloto.")
        output = BytesIO()
        with ZipFile(output, "w", ZIP_DEFLATED) as archive:
            archive.writestr("field-learning.json", payload)
        return output.getvalue()

    def import_bundle(self, data):
        if not isinstance(data, bytes) or len(data) > MAX_BUNDLE:
            raise ValueError("El archivo de aprendizaje es demasiado grande.")
        try:
            with ZipFile(BytesIO(data)) as archive:
                entries = archive.infolist()
                if len(entries) != 1 or entries[0].filename != "field-learning.json" or entries[0].file_size > MAX_BUNDLE:
                    raise ValueError("El ZIP de aprendizaje tiene un formato inválido.")
                payload = json.loads(archive.read(entries[0]))
        except (BadZipFile, KeyError, json.JSONDecodeError, UnicodeError, RuntimeError, OSError) as error:
            raise ValueError("No se pudo leer el aprendizaje.") from error
        if not isinstance(payload, dict) or payload.get("format") != "planilla-field-learning" or payload.get("version") != 1:
            raise ValueError("La versión de aprendizaje no es compatible.")
        sources = payload.get("sources")
        if not isinstance(sources, dict) or len(sources) > 50:
            raise ValueError("El archivo contiene demasiadas fuentes de aprendizaje.")
        cleaned = {}
        for origin, patterns in sources.items():
            if not isinstance(origin, str) or len(origin) != 32 or any(c not in "0123456789abcdef" for c in origin):
                raise ValueError("El identificador de aprendizaje es inválido.")
            if not isinstance(patterns, dict) or len(patterns) > 1000:
                raise ValueError("Hay demasiados patrones.")
            cleaned[origin] = {}
            for pattern, stats in patterns.items():
                try:
                    key = json.loads(pattern)
                except (json.JSONDecodeError, TypeError) as error:
                    raise ValueError("El patrón es inválido.") from error
                if (not isinstance(key, list) or len(key) != 10 or any(type(v) is not int for v in key)
                        or not all(0 <= v <= maximum for v, maximum in zip(key, (1, 2, 25, 200, 4, 3, 3, 4, 4, 15)))
                        or not isinstance(stats, dict) or set(stats) != set(_empty())):
                    raise ValueError("El patrón contiene datos no admitidos.")
                if any(type(stats[k]) is not int or not 0 <= stats[k] <= 100000 for k in (*COUNTS, "support")):
                    raise ValueError("Los contadores del patrón son inválidos.")
                if stats["support"] != sum(stats[k] for k in COUNTS):
                    raise ValueError("Los contadores no coinciden.")
                for name in ("sum", "min", "max"):
                    vector = stats[name]
                    if not isinstance(vector, list) or len(vector) != 4 or any(not _number(v) for v in vector):
                        raise ValueError("Las correcciones del patrón son inválidas.")
                for i in range(4):
                    if not -.2 <= stats["min"][i] <= stats["max"][i] <= .2 or abs(stats["sum"][i]) > .2 * stats["adjusted"] + .000001:
                        raise ValueError("La corrección excede los límites del piloto.")
                    if not stats["adjusted"] and any(stats[name][i] != 0 for name in ("sum", "min", "max")):
                        raise ValueError("Las estadísticas del patrón no son consistentes.")
                    if not stats["adjusted"] * stats["min"][i] - .000001 <= stats["sum"][i] <= stats["adjusted"] * stats["max"][i] + .000001:
                        raise ValueError("Las estadísticas del patrón no son consistentes.")
                cleaned[origin][_json(key)] = stats
        with self._db() as db:
            own = db.execute("SELECT value FROM settings WHERE name='origin'").fetchone()[0]
            for origin, patterns in cleaned.items():
                if origin != own:
                    db.execute("INSERT INTO imported_sources VALUES(?,?) ON CONFLICT(id) DO UPDATE SET patterns=excluded.patterns", (origin, _json(patterns)))
            db.execute("UPDATE settings SET value=CAST(value AS INTEGER)+1 WHERE name='revision'")
        return self.stats()
