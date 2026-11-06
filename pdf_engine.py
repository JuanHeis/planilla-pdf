"""Geometric form suggestions and native PDF form export."""

import math
import re

import pymupdf as pdf

from blank_cells import blank_label_fields
from digit_cells import digit_cell_fields
from scan_ocr import recognize_scan


MAX_PAGES = 50
MAX_FIELDS = 1000


def _open_pdf(data):
    try:
        document = pdf.open(stream=data, filetype="pdf")
    except (RuntimeError, ValueError) as error:
        raise ValueError("No se pudo abrir el PDF. Revisá que el archivo esté completo.") from error

    try:
        if not document.is_pdf:
            raise ValueError("El archivo debe ser un PDF.")
        if document.needs_pass or document.xref_get_key(-1, "Encrypt")[0] != "null":
            raise ValueError("Abrí una copia del PDF sin contraseña ni cifrado.")
        if not 1 <= document.page_count <= MAX_PAGES:
            raise ValueError(f"El PDF debe tener entre 1 y {MAX_PAGES} páginas.")
    except Exception:
        document.close()
        raise
    return document


def _existing_widgets(document):
    for page in document:
        widgets = list(page.widgets())
        for widget in widgets:
            if widget.field_type == pdf.PDF_WIDGET_TYPE_SIGNATURE and (
                document.xref_get_key(widget.xref, "V")[0] != "null"
                or document.get_sigflags() == 3
            ):
                raise ValueError("El PDF tiene una firma digital. Usá una copia sin firmar para agregar campos.")
        yield page, widgets


def _vector_geometry(page, drawings=None):
    boxes, horizontal, vertical = [], [], []
    for path in page.get_drawings() if drawings is None else drawings:
        has_stroke = path["type"] in {"s", "fs"} and path.get("stroke_opacity", 1) > 0.15
        if has_stroke and (path.get("width") or 0) > 4:
            continue
        for item in path["items"]:
            if item[0] == "re":
                box = item[1]
                if has_stroke and box.height >= 7 and box.width >= 7:
                    boxes.append(box)
                elif 0 <= box.height <= 2 and box.width >= 7:
                    horizontal.append(((box.y0 + box.y1) / 2, box.x0, box.x1))
                elif 0 <= box.width <= 2 and box.height >= 7:
                    vertical.append(((box.x0 + box.x1) / 2, box.y0, box.y1))
            if item[0] != "l" or not has_stroke:
                continue
            first, last = item[1:]
            if abs(first.y - last.y) <= 0.75 and abs(first.x - last.x) >= 7:
                horizontal.append(((first.y + last.y) / 2, min(first.x, last.x), max(first.x, last.x)))
            if abs(first.x - last.x) <= 0.75 and abs(first.y - last.y) >= 7:
                vertical.append(((first.x + last.x) / 2, min(first.y, last.y), max(first.y, last.y)))
    return boxes, horizontal, vertical


def _symbol_checkboxes(page):
    glyphs = []
    for span in page.get_texttrace():
        font = span["font"].lower()
        for codepoint, _, _, rect in span["chars"]:
            if codepoint in {0x2610, 0x25A1, 0x25FB, 0xF06F}:
                glyphs.append(pdf.Rect(rect))
            elif codepoint == 0xFFFD and ("wingdings" in font or "dingbats" in font):
                glyphs.append(pdf.Rect(rect))
    if not glyphs:
        return []
    display = page.get_displaylist(annots=False)
    scale = 4
    checkboxes = []
    for glyph in glyphs[:250]:
        if not (4 <= glyph.width <= 26 and 4 <= glyph.height <= 28):
            continue
        clip = glyph * page.rotation_matrix
        pixels = display.get_pixmap(matrix=pdf.Matrix(scale, scale), colorspace=pdf.csGRAY, clip=clip)
        width = pixels.width
        dark = [(i % width, i // width) for i, value in enumerate(pixels.samples) if value < 100]
        if not dark:
            continue
        left, right = min(x for x, _ in dark), max(x for x, _ in dark)
        top, bottom = min(y for _, y in dark), max(y for _, y in dark)
        w, h = right - left + 1, bottom - top + 1
        if h < 22 or not 0.80 <= w / h <= 1.20:
            continue
        # Exclude bullets and filled marks by requiring a mostly white center.
        center = sum(left + w * 0.2 < x < right - w * 0.2 and top + h * 0.2 < y < bottom - h * 0.2 for x, y in dark)
        sides = [
            sum(x <= left + 2 for x, _ in dark),
            sum(x >= right - 2 for x, _ in dark),
            sum(y <= top + 2 for _, y in dark),
            sum(y >= bottom - 2 for _, y in dark),
        ]
        if center <= w * h * 0.035 and min(sides) >= min(w, h) * 0.8:
            box = pdf.Rect(
                (pixels.x + left) / scale, (pixels.y + top) / scale,
                (pixels.x + right + 1) / scale, (pixels.y + bottom + 1) / scale,
            )
            checkboxes.append((box * page.derotation_matrix, "checkbox", "text", 0.89))
    return checkboxes


def _raster_geometry(page):
    scale = _bounded_scale(page.rect, 1.6)
    pixels = page.get_pixmap(matrix=pdf.Matrix(scale, scale), colorspace=pdf.csGRAY, annots=False)
    samples = pixels.samples
    dark_run = re.compile(rb"[\x00-\x90]{9,}")

    def runs(major_length, stride):
        strokes = []
        for index in range(major_length):
            row = samples[index * pixels.width : (index + 1) * pixels.width] if stride == 1 else samples[index::stride]
            for match in dark_run.finditer(row):
                if match.end() - match.start() >= 7 * scale:
                    strokes.append((index / scale, match.start() / scale, (match.end() - 1) / scale))
                    if len(strokes) >= 6500:
                        return []
        return _thin_strokes(strokes, 1.05 / scale)

    horizontal = runs(pixels.height, 1)
    vertical = runs(pixels.width, pixels.width) if pixels.width > 1 else []
    boxes = _closed_boxes(horizontal, vertical, tolerance=2.75)
    return boxes, horizontal, vertical


def _thin_strokes(strokes, gap):
    """Collapse parallel runs to one center line and discard broad bands."""
    active = []
    result = []
    for position, start, end in strokes:
        recent = []
        for stroke in active:
            if position - stroke[1] <= gap:
                recent.append(stroke)
            elif stroke[1] - stroke[0] <= 3:
                result.append(((stroke[0] + stroke[1]) / 2, stroke[2], stroke[3]))
        active = recent
        matching = next(
            (stroke for stroke in active if abs(stroke[2] - start) <= 2 and abs(stroke[3] - end) <= 2),
            None,
        )
        if matching is None:
            active.append([position, position, start, end])
        else:
            matching[1] = position
    result.extend(((first + last) / 2, start, end) for first, last, start, end in active if last - first <= 3)
    return result


def _closed_boxes(horizontal, vertical, tolerance):
    # A matching top and bottom only count when there is a side at both ends.
    vertical_by_x = {}
    for x, top, bottom in vertical:
        vertical_by_x.setdefault(round(x), []).append((x, top, bottom))
    boxes = []
    horizontal = sorted(horizontal)[:3000]
    for index, (top, left, right) in enumerate(horizontal):
        for next_index in range(index + 1, len(horizontal)):
            bottom, other_left, other_right = horizontal[next_index]
            height = bottom - top
            if height > 140:
                break
            if height < 7 or abs(left - other_left) > tolerance or abs(right - other_right) > tolerance:
                continue
            if all(
                any(
                    abs(x - side) <= tolerance and start <= top + tolerance and end >= bottom - tolerance
                    for bucket in range(round(side) - 3, round(side) + 4)
                    for x, start, end in vertical_by_x.get(bucket, ())
                )
                for side in (left, right)
            ):
                boxes.append(pdf.Rect(left, top, right, bottom))
    return boxes


def _vector_cells(horizontal, vertical):
    rows = []
    for y, left, right in sorted(horizontal)[:3000]:
        if not rows or y - rows[-1][0] > 1.1:
            rows.append((y, []))
        rows[-1][1].append((left, right))

    for _, segments in rows:
        joined = []
        for left, right in sorted(segments):
            if joined and left <= joined[-1][1] + 1.75:
                joined[-1][1] = max(right, joined[-1][1])
            else:
                joined.append([left, right])
        segments[:] = joined

    side_groups = []
    for x, start, end in sorted(vertical)[:3000]:
        if side_groups and x - side_groups[-1][0] <= 0.25:
            side_groups[-1][1].append((start, end))
        else:
            side_groups.append((x, [(start, end)]))
    sides = []
    for x, pieces in side_groups:
        for start, end in sorted(pieces):
            if sides and x == sides[-1][0] and start <= sides[-1][2] + 4:
                sides[-1][2] = max(end, sides[-1][2])
            else:
                sides.append([x, start, end])

    cells = []
    for index, (top, top_segments) in enumerate(rows):
        for next_index in range(index + 1, len(rows)):
            bottom, bottom_segments = rows[next_index]
            height = bottom - top
            if height > 65:
                break
            if height < 9:
                continue
            cuts = sorted(
                x for x, start, end in sides
                if start <= top + 3.5 and end >= bottom - 3.5
                and min(bottom, end) - max(top, start) >= height * 0.65
            )
            cuts = [x for i, x in enumerate(cuts) if not i or x - cuts[i - 1] > 1.5]
            for left, right in zip(cuts, cuts[1:]):
                if right - left < 15:
                    continue
                if all(
                    any(a <= left + 2 and b >= right - 2 for a, b in segments)
                    for segments in (top_segments, bottom_segments)
                ):
                    cells.append(pdf.Rect(left, top, right, bottom))
    return cells


def _cell_fields(cells, labels, horizontal, boxes):
    results = []
    for cell in cells:
        if any(
            cell.y0 + 3.5 < y < cell.y1 - 3.5
            and min(cell.x1, right) - max(cell.x0, left) > cell.width * 0.55
            for y, left, right in horizontal
        ):
            continue
        inside = [(rect, word) for rect, word in labels if cell.contains((rect.tl + rect.br) / 2)]
        inner = cell + (1, 1, -1, -1)
        if inside:
            top = min(rect.y0 for rect, _ in inside)
            bottom = max(rect.y1 for rect, _ in inside)
            if bottom - top > 20 or any(
                cell.contains(box) and 7 <= box.width <= 25 and 0.75 <= box.width / box.height <= 1.3
                for box in boxes
            ):
                continue
            if top <= cell.y0 + 7 and inner.y1 - bottom >= 7:
                inner.y0 = bottom + 0.5
            else:
                text_right = max(rect.x1 for rect, _ in inside)
                if bottom - top > 12 or text_right - cell.x0 > cell.width * 0.48 or inner.x1 - text_right < 25:
                    continue
                # Group titles can leave a wide blank to their right above labeled subcolumns.
                subcolumns = [
                    child for child in cells
                    if abs(child.y0 - cell.y1) <= 1 and child.x0 >= cell.x0 - 1
                    and child.x1 <= cell.x1 + 1 and child.width < cell.width - 5
                    and any(child.contains((rect.tl + rect.br) / 2) for rect, _ in labels)
                ]
                if len(subcolumns) >= 2:
                    continue
                inner.x0 = text_right + 3
                inner.y0 = max(inner.y0, top)
        elif not any(
            cell.y0 - 13 <= rect.y1 <= cell.y0 + 0.5
            and cell.x0 <= (rect.x0 + rect.x1) / 2 <= cell.x1
            for rect, _ in labels
        ):
            continue
        # An empty square in a labeled table row is a short text column.
        text_column = not inside and cell.width <= 25 and any(
            abs(neighbor.y0 - cell.y0) <= 1 and abs(neighbor.y1 - cell.y1) <= 1
            and neighbor.width > 25
            and min(abs(neighbor.x1 - cell.x0), abs(neighbor.x0 - cell.x1)) <= 1
            for neighbor in cells
        )
        results.append((inner, "text", "vector", 0.94 if text_column else 0.90))
    return results


def _merged_shaded_fields(cells, blocked, labels):
    results = []
    for header in cells:
        header_words = [rect for rect, _ in labels if header.contains((rect.tl + rect.br) / 2)]
        if not header_words or max(rect.y1 for rect in header_words) < header.y1 - 4:
            continue
        children = sorted(
            (cell for cell in cells if abs(cell.y0 - header.y1) <= 1
             and header.x0 - 1 <= cell.x0 and cell.x1 <= header.x1 + 1
             and any((cell & area).get_area() >= cell.get_area() * 0.6 for area in blocked)),
            key=lambda cell: cell.x0,
        )
        if len(children) < 2 or abs(children[0].x0 - header.x0) > 1 or abs(children[-1].x1 - header.x1) > 1:
            continue
        if any(
            abs(left.x1 - right.x0) > 1 or abs(left.y1 - right.y1) > 1
            for left, right in zip(children, children[1:])
        ):
            continue
        combined = children[0] | children[-1]
        if any(combined.contains((rect.tl + rect.br) / 2) for rect, _ in labels):
            continue
        results.append((combined + (1, 1, -1, -1), "text", "vector", 0.91))
    return results


def _occupied(box, obstacles):
    for obstacle in obstacles:
        overlap = box & obstacle
        if overlap.get_area() > 0.30 * min(box.get_area(), obstacle.get_area()):
            return True
    return False


def _nearby_label(box, words, kind):
    nearby = []
    center_y = (box.y0 + box.y1) / 2
    for rect, word in words:
        word_center = (rect.y0 + rect.y1) / 2
        if abs(center_y - word_center) <= max(8, box.height / 2):
            if 0 <= box.x0 - rect.x1 <= 85:
                nearby.append((box.x0 - rect.x1 + (10 if kind == "checkbox" else 0), rect, word, "left"))
            if kind == "checkbox" and 0 <= rect.x0 - box.x1 <= 65:
                nearby.append((rect.x0 - box.x1, rect, word, "right"))
        if 0 <= box.y0 - rect.y1 <= 15 and box.x0 - 8 <= rect.x0 <= box.x0 + 50:
            nearby.append((10 + box.y0 - rect.y1 + abs(rect.x0 - box.x0) / 3, rect, word, "above"))
    if not nearby:
        return ""
    _, closest, token, side = min(nearby, key=lambda item: item[0])
    nearby_words = sorted(
        ((rect, word) for rect, word in words if abs(rect.y0 - closest.y0) <= 3),
        key=lambda item: item[0].x0,
        reverse=side == "left",
    )
    phrase = [token]
    end = closest.x0 if side == "left" else closest.x1
    for rect, word in nearby_words:
        gap = end - rect.x1 if side == "left" else rect.x0 - end
        if gap < 0:
            continue
        if gap > 9 or len(phrase) >= 6 or (side == "left" and word.endswith(":")):
            break
        phrase.append(word)
        end = rect.x0 if side == "left" else rect.x1
    if side == "left":
        phrase.reverse()
    return " ".join(phrase).strip(" :_-")[:80]


def _leader_fields(page, words):
    marker_run = r"[._·…]+(?:[-/][._·…]+)*"
    if not any(re.search(r"[._·…]{3}|…", word) for _, word in words):
        return [], words

    leaders = []
    flags = pdf.TEXTFLAGS_RAWDICT & ~pdf.TEXT_PRESERVE_IMAGES
    for block in page.get_text("rawdict", flags=flags)["blocks"]:
        for line in block.get("lines", ()):
            if abs(line["dir"][1]) > 0.02 or line["dir"][0] < 0:
                continue
            glyphs = [glyph for span in line["spans"] for glyph in span["chars"]]
            value = "".join(glyph["c"] for glyph in glyphs)
            for match in re.finditer(marker_run, value):
                marker = match.group().replace("…", "...")
                if marker.count("_") < 3 and len(re.sub(r"[^.·]", "", marker)) < 5:
                    continue
                # Page references in an index are printed on the same line after the leaders.
                if value[:match.start()].strip() and value[match.end():].strip().isdecimal():
                    continue
                first, last = match.span()
                box = pdf.Rect(glyphs[first]["bbox"])
                for glyph in glyphs[first + 1:last]:
                    box |= pdf.Rect(glyph["bbox"])
                if box.width >= 15:
                    leaders.append(box)

    clean_words = []
    for rect, word in words:
        if any(char.isalnum() for char in word) and re.search(marker_run, word):
            for leader in leaders:
                overlap = rect & leader
                if overlap.height < rect.height * 0.7 or overlap.width < 3:
                    continue
                # Keep printed labels as obstacles without covering their attached response space.
                if overlap.x1 >= rect.x1 - 0.5:
                    rect = pdf.Rect(rect.x0, rect.y0, overlap.x0, rect.y1)
                    word = re.sub(marker_run + r"$", "", word)
                elif overlap.x0 <= rect.x0 + 0.5:
                    rect = pdf.Rect(overlap.x1, rect.y0, rect.x1, rect.y1)
                    word = re.sub("^" + marker_run, "", word)
        if not rect.is_empty and word:
            clean_words.append((rect, word))
    return [(box, "text", "text", 0.91) for box in leaders], clean_words


def _side_label(box, labels, distance):
    return any(
        abs((rect.y0 + rect.y1 - box.y0 - box.y1) / 2) <= max(5, box.height * 0.65)
        and (0 <= box.x0 - rect.x1 <= distance or 0 <= rect.x0 - box.x1 <= distance)
        for rect, _ in labels
    )


def _fill_fields(drawings, labels):
    fills = []
    fields = []
    for path in drawings:
        if path["type"] not in {"f", "fs"} or path["fill"] is None or path.get("fill_opacity", 1) < 0.8:
            continue
        lightness = sum(path["fill"]) / len(path["fill"])
        for item in path["items"]:
            if item[0] != "re":
                continue
            box = item[1]
            if box.width < 7 or box.height < 7:
                continue
            if (path["type"] == "f" and lightness >= 0.72
                    and 7 <= box.height <= 120
                    and any(old.contains(box) and old.get_area() > box.get_area() * 1.18
                            and lightness - old_lightness >= 0.055 for old, old_lightness in fills)):
                if 0.75 <= box.width / box.height <= 1.3 and box.width <= 20:
                    if _side_label(box, labels, 25):
                        fields.append((box, "checkbox", "vector", 0.91))
                elif box.width >= 28 and box.height >= 14:
                    inside = [rect for rect, _ in labels if box.contains((rect.tl + rect.br) / 2)]
                    if inside:
                        # Restrict to a short label at the top of a clear response region.
                        end = max(rect.y1 for rect in inside)
                        if end - box.y0 <= 24 and box.y1 - end >= 8:
                            fields.append((pdf.Rect(box.x0 + 1, end + 0.5, box.x1 - 1, box.y1 - 1),
                                           "text", "vector", 0.90))
            fills.append((box, lightness))
    return fields


def _wide_option_fields(boxes, cells, labels):
    # A wide checkbox is ambiguous in a table, so require a repeated option shape outside the grid.
    options = [box for box in boxes if 7 <= box.height <= 16 and 1.3 < box.width / box.height <= 2.2
               and box.width <= 25 and not any((box & cell).get_area() >= box.get_area() * 0.90 for cell in cells)]
    fields = []
    for box in options:
        if not any(other != box and abs(other.width - box.width) <= 1 and abs(other.height - box.height) <= 1
                   and (abs(other.x0 - box.x0) <= 1 or abs(other.y0 - box.y0) <= 2)
                   and (_side_label(box, labels, 30) or _side_label(other, labels, 30))
                   for other in options):
            continue
        short_value = any(
            0 <= box.x0 - rect.x1 <= 12 and abs(rect.y0 + rect.y1 - box.y0 - box.y1) <= 10
            and re.fullmatch(r"(?:grado(?:/año)?|año|número|n[º°]|fecha)[:.]?", word, re.I)
            for rect, word in labels
        )
        fields.append((box, "text" if short_value else "checkbox", "vector", 0.91))
    return fields


def _has_frame_sides(y, left, right, vertical, extends_below):
    # The side strokes of a frame can start just inside its horizontal edge.
    return all(
        any(abs(x - edge) <= 3 and start - 6 <= y <= end + 3
            and (end - y if extends_below else y - start) >= 7
            for x, start, end in vertical)
        for edge in (left, right)
    )


def _suggestions(boxes, horizontal, words, bounds, source, cells=(), vertical=()):
    results = []
    for box in boxes:
        ratio = box.width / box.height
        if 7 <= box.height <= 25 and 0.75 <= ratio <= 1.30 and box.width <= 25:
            results.append((box, "checkbox", source, 0.92 if source == "vector" else 0.72))
        elif 9 <= box.height <= 140 and box.width >= 28 and ratio >= 1.35 and box.width < bounds.width * 0.92:
            inner = pdf.Rect(box.x0 + 1, box.y0 + 1, box.x1 - 1, box.y1 - 1)
            labels_inside = [
                rect for rect, word in words
                if any(char.isalnum() for char in word)
                and (rect & inner).get_area() > rect.get_area() * 0.30
            ]
            if labels_inside:
                text_end = max(rect.y1 for rect in labels_inside)
                if text_end > box.y0 + 13 or box.y1 - text_end < 8:
                    continue
                inner.y0 = text_end + 0.25
            if any(
                inner.y0 + 3 < y < inner.y1 - 3
                and min(inner.x1, right) - max(inner.x0, left) > inner.width * 0.65
                for y, left, right in horizontal
            ):
                continue
            results.append((inner, "text", source, 0.87 if source == "vector" else 0.67))
    table_boxes = (*boxes, *cells)
    for y, left, right in horizontal:
        if not 30 <= right - left <= bounds.width * 0.78:
            continue
        if _has_frame_sides(y, left, right, vertical, extends_below=False):
            upper_edge = max(
                (top for top, start, end in horizontal
                 if y - 30 <= top <= y - 7 and start <= left + 2 and end >= right - 2),
                default=None,
            )
            if upper_edge is None:
                continue
            inner_cell = pdf.Rect(left + 1, upper_edge + 1, right - 1, y - 1)
            if _occupied(inner_cell, (rect for rect, word in words if any(char.isalnum() for char in word))):
                continue
        elif _has_frame_sides(y, left, right, vertical, extends_below=True):
            continue
        label_bottom = max(
            (rect.y1 for rect, word in words
             if left - 2 <= rect.x0 <= left + 8 and y - 26 <= rect.y1 <= y - 8
             and any(char.isalnum() for char in word)),
            default=0,
        )
        if any(
            min(right, box.x1) - max(left, box.x0) >= 0.75 * (right - left)
            and (abs(y - box.y1) <= 3 or (abs(y - box.y0) <= 3 and not label_bottom))
            for box in table_boxes
        ):
            continue
        top = max(
            (box.y1 + 0.5 for box in table_boxes
             if y - 14 < box.y1 < y - 7
             and min(right, box.x1) - max(left, box.x0) >= 0.65 * (right - left)),
            default=y - 14,
        )
        line_box = pdf.Rect(left, max(top, label_bottom + 0.5), right, y - 0.5)
        results.append((line_box, "text", source, 0.78 if source == "vector" else 0.59))
    return results


def _bounded_scale(bounds, scale):
    return min(scale, 2050 / max(bounds.width, bounds.height, 1))


def detect_pdf(data):
    pages, fields, warnings = [], [], []
    existing_fields = []
    with _open_pdf(data) as document:
        for page, widgets in _existing_widgets(document):
            bounds = page.rect * page.derotation_matrix
            pages.append({"width": page.rect.width, "height": page.rect.height, "rotation": page.rotation})
            words = [(pdf.Rect(*word[:4]), word[4]) for word in page.get_text("words")]
            leaders, words = _leader_fields(page, words)
            labels = [(rect, word) for rect, word in words if any(char.isalnum() for char in word)]
            existing = [widget.rect for widget in widgets]
            for widget in widgets:
                rect = widget.rect * page.rotation_matrix
                kind = {
                    pdf.PDF_WIDGET_TYPE_TEXT: "text",
                    pdf.PDF_WIDGET_TYPE_CHECKBOX: "checkbox",
                    pdf.PDF_WIDGET_TYPE_RADIOBUTTON: "radio",
                }.get(widget.field_type, "other")
                existing_fields.append({
                    "id": f"existing-{len(existing_fields) + 1}",
                    "page": page.number,
                    "x": round(rect.x0, 2), "y": round(rect.y0, 2),
                    "width": round(rect.width, 2), "height": round(rect.height, 2),
                    "type": kind,
                    "name": widget.field_label or widget.field_name or "Campo original",
                })
            headers = [(rect, word.casefold().strip("():.")) for rect, word in words if rect.y1 < 100]
            if any(word == "dorso" for _, word in headers) and any(word == "instrucciones" for _, word in headers):
                continue
            drawings = page.get_drawings()
            blocked = []
            if re.search(r"\bno\s+(?:llene|rellene|complete)\s+[^.\n]{0,60}\bgris\b", page.get_text(), re.I):
                blocked = [
                    path["rect"] for path in drawings
                    if path["type"] in {"f", "fs"} and path["fill"] is not None
                    and 0.45 <= min(path["fill"]) <= max(path["fill"]) <= 0.91
                    and max(path["fill"]) - min(path["fill"]) < 0.04
                    and path["rect"].width >= 15 and path["rect"].height >= 7
                ]
            boxes, horizontal, vertical = _vector_geometry(page, drawings)
            boxes.extend(_closed_boxes(horizontal, vertical, tolerance=1.5))
            cells = _vector_cells(horizontal, vertical)
            candidates = _suggestions(boxes, horizontal, words, bounds, "vector", cells, vertical)
            candidates.extend(blank_label_fields(page, vertical, labels))
            candidates.extend(digit_cell_fields(cells, boxes, labels))
            candidates.extend(_cell_fields(cells, labels, horizontal, boxes))
            candidates.extend(leaders)
            candidates.extend(_fill_fields(drawings, labels))
            candidates.extend(_wide_option_fields(boxes, cells, labels))
            candidates.extend(_symbol_checkboxes(page))
            shaded_fields = _merged_shaded_fields(cells, blocked, labels) if blocked else []
            candidates.extend(shaded_fields)

            image_coverage = max(
                ((pdf.Rect(info["bbox"]) & bounds).get_area() for info in page.get_image_info()),
                default=0,
            )
            scan_labels = []
            if image_coverage > bounds.get_area() * 0.38:
                if len(labels) < 6:
                    scan_labels = recognize_scan(page)
                raster_boxes, raster_lines, _ = _raster_geometry(page)
                visual = _suggestions(raster_boxes, raster_lines, [], page.rect, "raster")
                candidates.extend(
                    (box * page.derotation_matrix, kind, source, confidence)
                    for box, kind, source, confidence in visual
                )
                method = " Se usó OCR para ubicar el texto impreso." if scan_labels else ""
                warnings.append(f"Página {page.number + 1}: se buscaron líneas y casillas en la imagen.{method} Revisá la ubicación sugerida.")

            accepted = []
            obstacles = [rect for rect, _ in labels] + existing
            scan_obstacles = [rect for rect, _ in scan_labels]
            for box, kind, source, confidence in sorted(candidates, key=lambda item: -item[3]):
                box &= bounds
                if box.width < 5 or box.height < 5 or _occupied(box, obstacles):
                    continue
                if source == "raster" and _occupied(box, scan_obstacles):
                    continue
                if (box, kind, source, confidence) not in shaded_fields and any(
                    (box & area).get_area() > box.get_area() * 0.5 for area in blocked
                ):
                    continue
                if any(_occupied(box, [accepted_box]) for accepted_box, *_ in accepted):
                    continue
                naming_labels = labels + scan_labels if source == "raster" else labels
                name = _nearby_label(box, naming_labels, kind)
                visual_box = box * page.rotation_matrix
                accepted.append((box, visual_box, kind, source, confidence, name))

            for _, box, kind, source, confidence, name in sorted(accepted, key=lambda item: (item[1].y0, item[1].x0)):
                if len(fields) >= MAX_FIELDS:
                    break
                number = len(fields) + 1
                fields.append({
                    "id": f"detected-{number}", "page": page.number,
                    "x": round(box.x0, 2), "y": round(box.y0, 2),
                    "width": round(box.width, 2), "height": round(box.height, 2),
                    "type": kind, "source": source, "confidence": confidence,
                    "name": name or f"{'Casilla' if kind == 'checkbox' else 'Campo'} {number}",
                })
    if not fields and not existing_fields:
        warnings.append("No se detectaron campos nuevos. Podés agregarlos manualmente.")
    if len(fields) >= MAX_FIELDS:
        warnings.append(f"La detección se limitó a {MAX_FIELDS} campos.")
    return {"pages": pages, "fields": fields, "existingFields": existing_fields, "warnings": warnings}


def render_page(data, page, scale=1.5):
    with _open_pdf(data) as document:
        if type(page) is not int or not 0 <= page < document.page_count:
            raise ValueError("La página solicitada no existe.")
        source = document[page]
        zoom = _bounded_scale(source.rect, scale)
        return source.get_pixmap(matrix=pdf.Matrix(zoom, zoom)).tobytes("png")


def _export_fields(document, fields):
    if not isinstance(fields, list) or len(fields) > MAX_FIELDS:
        raise ValueError(f"Podés agregar hasta {MAX_FIELDS} campos por PDF.")
    for index, field in enumerate(fields, 1):
        if not isinstance(field, dict):
            raise ValueError(f"El campo {index} no tiene un formato válido.")
        page = field.get("page")
        if type(page) is not int or not 0 <= page < document.page_count:
            raise ValueError(f"El campo {index} pertenece a una página inexistente.")
        kind = field.get("type")
        if kind not in ("text", "checkbox"):
            raise ValueError(f"El tipo del campo {index} no es válido.")
        coords = [field.get(key) for key in ("x", "y", "width", "height")]
        if not all(type(value) in (int, float) and abs(value) <= 1e6 and math.isfinite(value) for value in coords):
            raise ValueError(f"Las coordenadas del campo {index} no son válidas.")
        x, y, width, height = coords
        bounds = document[page].rect
        box = pdf.Rect(x, y, x + width, y + height)
        if width < 4 or height < 4 or x < -0.05 or y < -0.05 or box.x1 > bounds.x1 + 0.05 or box.y1 > bounds.y1 + 0.05:
            raise ValueError(f"El campo {index} es demasiado pequeño o se sale de la página.")
        label = field.get("name", field.get("label", ""))
        if not isinstance(label, str):
            raise ValueError(f"El nombre del campo {index} no es válido.")
        yield page, kind, box & bounds, label.replace("\x00", "").strip()[:120], index


def _set_default_font(document):
    catalog = document.pdf_catalog()
    form_type, form_value = document.xref_get_key(catalog, "AcroForm")
    form_xref = int(form_value.split()[0]) if form_type == "xref" else catalog
    form_prefix = "" if form_type == "xref" else "AcroForm/"
    default_key = form_prefix + "DA"
    if document.xref_get_key(form_xref, default_key)[0] == "null":
        document.xref_set_key(form_xref, default_key, pdf.get_pdf_str("/Helv 0 Tf 0 g"))

    resource_xref, resource_prefix = form_xref, form_prefix
    for key in ("DR", "Font"):
        kind, value = document.xref_get_key(resource_xref, resource_prefix + key)
        if kind == "xref":
            resource_xref, resource_prefix = int(value.split()[0]), ""
        else:
            resource_prefix += key + "/"
    font_key = resource_prefix + "Helv"
    if document.xref_get_key(resource_xref, font_key)[0] == "null":
        font = document.get_new_xref()
        document.update_object(font, "<</Type/Font/Subtype/Type1/BaseFont/Helvetica/Encoding/WinAnsiEncoding>>")
        document.xref_set_key(resource_xref, font_key, f"{font} 0 R")


def export_pdf(data, fields):
    with _open_pdf(data) as document:
        definitions = list(_export_fields(document, fields))
        if not definitions:
            return data
        existing_names = {widget.field_name for _, widgets in _existing_widgets(document) for widget in widgets}
        for page_number, kind, box, label, index in definitions:
            page = document[page_number]
            widget = pdf.Widget()
            widget.field_type = pdf.PDF_WIDGET_TYPE_CHECKBOX if kind == "checkbox" else pdf.PDF_WIDGET_TYPE_TEXT
            stem = re.sub(r"[^\w-]+", "_", label, flags=re.ASCII).strip("_") or "campo"
            widget.field_name = f"{stem}_{page_number + 1}_{index}"
            while widget.field_name in existing_names:
                widget.field_name += "_"
            existing_names.add(widget.field_name)
            widget.field_label = label or f"Campo {index}"
            widget.rect = box * page.derotation_matrix
            widget.border_width = 0
            if kind == "checkbox":
                widget.field_value = "Off"
            else:
                widget.field_value = ""
                widget.text_font = "Helv"
                widget.text_fontsize = 0
                if widget.rect.height >= 30:
                    widget.field_flags |= pdf.PDF_TX_FIELD_IS_MULTILINE
            page.add_widget(widget)
        if any(kind == "text" for _, kind, _, _, _ in definitions):
            _set_default_font(document)
        return document.tobytes(garbage=3, deflate=True)
