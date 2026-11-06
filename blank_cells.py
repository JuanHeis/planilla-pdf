"""Recognize unruled spaces in a verified form section."""

import pymupdf as pdf


_ADDRESS_LABELS = (
    "Calle:", "Número:", "Piso:", "Depto:", "Código Postal:",
    "Localidad:", "Provincia:", "Teléfono:", "Email:",
)


def blank_label_fields(page, vertical, labels):
    """Return candidates for the address section of ANSES PS.1.47 v1.1."""
    if page.number != 0:
        return []

    # shortcut: unruled whitespace is accepted only for this verified layout.
    header = {word for rect, word in labels if rect.y1 <= 75}
    if not {"PS.1.47", "Libreta", "Versión", "1.1"} <= header:
        return []

    flags = pdf.TEXTFLAGS_DICT & ~pdf.TEXT_PRESERVE_IMAGES
    lines = [
        (pdf.Rect(line["bbox"]), " ".join(
            "".join(span["text"] for span in line["spans"]).split()
        ))
        for block in page.get_text("dict", flags=flags)["blocks"]
        for line in block.get("lines", ())
    ]
    heading = next((rect for rect, text in lines if text == "Domicilio del Titular:"), None)
    if heading is None:
        return []

    nearby = sorted(
        ((rect, text) for rect, text in lines
         if abs(rect.x0 - heading.x0 - 4) <= 2 and 7 <= rect.y0 - heading.y0 <= 110),
        key=lambda line: line[0].y0,
    )
    if tuple(text for _, text in nearby) != _ADDRESS_LABELS:
        return []
    rows = [rect for rect, _ in nearby]
    if any(not 11 <= row.y0 - previous.y0 <= 13 for previous, row in zip(rows, rows[1:])):
        return []

    divider = min(
        (x for x, top, bottom in vertical if top <= heading.y0 and bottom >= rows[-1].y1
         and 90 <= x - heading.x1 <= 115),
        default=None,
    )
    if divider is None:
        return []
    cellular = next(
        (rect for rect, text in lines if text == "Es Celular?"
         and 4 <= rect.y0 - rows[-2].y0 <= 9 and 10 <= divider - rect.x1 <= 40),
        None,
    )
    if cellular is None:
        return []

    results = []
    for index, row in enumerate(rows):
        right = cellular.x0 - 3 if index >= len(rows) - 2 else divider - 6
        region = pdf.Rect(row.x1 + 4, row.y0 + 1, right, row.y1 - 1)
        if region.width < 40:
            continue
        # Render only the target area to catch printed ink, stamps, or annotations.
        pixels = page.get_pixmap(
            matrix=pdf.Matrix(2, 2), clip=region * page.rotation_matrix,
            colorspace=pdf.csGRAY, alpha=False,
        )
        if pixels.samples and min(pixels.samples) >= 245:
            results.append((region, "text", "vector", 0.88))
    return results
