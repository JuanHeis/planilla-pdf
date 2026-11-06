"""Local Spanish OCR used as a second source of labels on image-heavy pages."""

import os
from pathlib import Path

import pymupdf as pdf


def recognize_scan(page, *, review=False):
    """Return printed word boxes in unrotated PDF coordinates."""
    bundled = Path(__file__).with_name("tessdata")
    configured = os.environ.get("TESSDATA_PREFIX")
    tessdata = Path(configured) if configured else bundled
    if not (tessdata / "spa.traineddata").is_file():
        if review:
            raise ValueError("No se encontró el modelo de OCR en español.")
        return []

    # Limit large sheets without sacrificing the detail of ordinary form scans.
    longest_side = max(page.rect.width, page.rect.height, 1)
    dpi = max(1, min(200, int(2600 * 72 / longest_side)))
    try:
        recognized = page.get_textpage_ocr(
            language="spa", dpi=dpi, full=True, tessdata=str(tessdata.resolve())
        )
        return [
            (pdf.Rect(*word[:4]), word[4])
            for word in page.get_text("words", textpage=recognized)
            if review or sum(character.isalpha() for character in word[4]) >= 2
        ]
    except (RuntimeError, ValueError) as error:
        if review:
            raise ValueError("El OCR no pudo leer esta página.") from error
        return []
