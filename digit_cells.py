"""Find a single writable number box in a row of printed digit guides."""

import re


_IDENTIFIER = re.compile(
    r"(?:legajo|d\.?n\.?i|c\.?u\.?i\.?[lt]|documento|matr[ií]cula|"
    r"tel[eé]fono|n[uú]mero|n[°º]|c\.?p)\.?:?",
    re.IGNORECASE,
)


def digit_cell_fields(cells, boxes, labels):
    rows = {}
    for cell in (*boxes, *cells):
        if 5 <= cell.width <= 25 and 7 <= cell.height <= 28 and cell.width <= 1.7 * cell.height:
            rows.setdefault((round(cell.y0), round(cell.y1)), []).append(cell)

    results = []
    for row in rows.values():
        groups = []
        for cell in sorted(row, key=lambda item: (item.x0, item.x1)):
            if not groups:
                groups.append([cell])
                continue
            previous = groups[-1][-1]
            if abs(cell.x0 - previous.x0) <= 1 and abs(cell.x1 - previous.x1) <= 1:
                continue
            if abs(cell.x0 - previous.x1) <= 1 and abs(cell.width - previous.width) <= 2:
                groups[-1].append(cell)
            else:
                groups.append([cell])

        for group in groups:
            if len(group) < 3:
                continue
            box = group[0] | group[-1]
            if any(box.contains((rect.tl + rect.br) / 2) for rect, _ in labels):
                continue
            if not any(
                _IDENTIFIER.fullmatch(word)
                and (
                    (0 <= box.x0 - rect.x1 <= 90 and box.y0 <= (rect.y0 + rect.y1) / 2 <= box.y1)
                    or (0 <= box.y0 - rect.y1 <= 12 and box.x0 - 2 <= (rect.x0 + rect.x1) / 2 <= box.x1)
                )
                for rect, word in labels
            ):
                continue
            results.append((box + (1, 1, -1, -1), "text", "vector", 0.98))
    return results
