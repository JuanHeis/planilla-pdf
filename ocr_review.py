"""Review the original OCR words without changing the source PDF."""
from hashlib import sha256

from ocr_learning import OcrCorrector
import ocr_review_store as store
from pdf_engine import _open_pdf
from scan_ocr import recognize_scan


def describe(data):
    with _open_pdf(data) as doc:
        return {"pages": [
            {"width": page.rect.width, "height": page.rect.height, "rotation": page.rotation}
            for page in doc
        ], "warnings": []}


def get_words(data, index, cache):
    if index in cache:
        return cache[index]
    with _open_pdf(data) as doc:
        if not 0 <= index < len(doc):
            raise ValueError("La página solicitada no existe.")
        page = doc[index]
        words = []
        for rect, text in recognize_scan(page, review=True):
            if not text or len(text) > 256 or not text.isprintable():
                continue
            rect = (rect * page.rotation_matrix) & page.rect
            if rect.is_empty:
                continue
            words.append({"id": str(len(words)), "original": text,
                          "x": round(rect.x0, 2), "y": round(rect.y0, 2),
                          "width": round(rect.width, 2), "height": round(rect.height, 2)})
        if len(words) > 6000:
            raise ValueError("La página tiene demasiadas palabras para revisar.")
        cache[index] = words
        if len(cache) > 4:
            cache.pop(next(iter(cache)))
        return words


def reviewed_page(data, index, cache):
    words = get_words(data, index, cache)
    review, examples = store.page_review(sha256(data).hexdigest(), index)
    saved = {row["id"]: row for row in review["changes"]} if review else {}
    model = OcrCorrector(examples)

    def display(word):
        original = word["original"]
        change = saved.get(word["id"])
        if change and change["original"] != original:
            change = None
        return {**word, "suggested": model.suggest(original),
                "text": change["corrected"] if change else original}

    return {"words": [display(word) for word in words], "reviewed": review is not None}


def save_review(data, index, cache, corrections):
    words = get_words(data, index, cache)
    if not isinstance(corrections, list) or len(corrections) > len(words):
        raise ValueError("La lista de correcciones es inválida.")
    originals = {word["id"]: word["original"] for word in words}
    seen = set()
    changes = []
    for row in corrections:
        key = row.get("id") if isinstance(row, dict) else None
        text = row.get("text") if isinstance(row, dict) else None
        if (not isinstance(key, str) or key not in originals or key in seen
                or not isinstance(text, str) or len(text) > 256
                or not all(ch.isprintable() for ch in text)):
            raise ValueError("Hay una corrección inválida en esta página.")
        seen.add(key)
        if text != originals[key]:
            changes.append({"id": key, "original": originals[key], "corrected": text})
    fingerprint = sha256(data).hexdigest()
    previous, examples = store.page_review(fingerprint, index)
    previously_kept = {
        (row["id"], row["original"])
        for row in (previous["changes"] if previous else [])
        if row["original"] == row["corrected"]
    }
    model = OcrCorrector(examples)
    changed = {row["id"] for row in changes}
    # A confirmed page only keeps unedited words that could affect a learned suggestion.
    for key, original in originals.items():
        if key not in changed and ((key, original) in previously_kept or model.suggest(original) != original):
            changes.append({"id": key, "original": original, "corrected": original})
    changes.sort(key=lambda change: int(change["id"]))
    learning = store.save(fingerprint, index, len(words), changes)
    return {**reviewed_page(data, index, cache), "learning": learning}
