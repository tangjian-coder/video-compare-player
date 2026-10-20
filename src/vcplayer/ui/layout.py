"""Grid layout model for the 2-4 video panes.

A layout is a list of rows; each row is a non-empty list of view ids
("a".."d"). Positions are derived from row lengths only, so the model
needs no Qt and stays easy to test: every row spans GRID_COLUMNS and the
row is split evenly (12 is divisible by 1, 2, 3 and 4).

Default per view count: 2 = 1x2, 3 = 1x3, 4 = 2x2. An arranged layout is
kept as-is when a view is added or removed; only a layout still equal to
the default transitions to the next default.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence

GRID_COLUMNS = 12
MAX_VIEWS = 4

Row = list[str]
Layout = list[Row]

ZONE_LEFT = "left"
ZONE_RIGHT = "right"
ZONE_TOP = "top"
ZONE_BOTTOM = "bottom"
ZONE_CENTER = "center"

_ZONE_PREPOSITIONS = {
    ZONE_LEFT: "left of",
    ZONE_RIGHT: "right of",
    ZONE_TOP: "above",
    ZONE_BOTTOM: "below",
}


def zone_preposition(zone: str) -> str:
    """Human-readable drop position for status messages."""
    return _ZONE_PREPOSITIONS.get(zone, "beside")


def default_layout(view_ids: Sequence[str]) -> Layout:
    """Default arrangement for the given number of views."""
    ids = list(view_ids)[:MAX_VIEWS]
    if not ids:
        return []
    if len(ids) <= 3:
        return [ids]
    return [ids[:2], ids[2:]]


def presets(view_ids: Sequence[str]) -> list[tuple[str, Layout]]:
    """Named arrangements offered in the pane context menu."""
    ids = list(view_ids)
    n = len(ids)
    if n == 2:
        return [
            ("Side by side", [ids]),
            ("Stacked", [[ids[0]], [ids[1]]]),
        ]
    if n == 3:
        return [
            ("Row (1x3)", [ids]),
            ("Top 2 + 1", [ids[:2], ids[2:]]),
            ("Stacked (3 rows)", [[vid] for vid in ids]),
        ]
    if n == 4:
        return [
            ("Grid (2x2)", [ids[:2], ids[2:]]),
            ("Row (1x4)", [ids]),
            ("Top 3 + 1", [ids[:3], ids[3:]]),
        ]
    return []


def cells(layout: Layout) -> Iterator[tuple[str, int, int, int]]:
    """Yield (view id, row, column, column span) for every pane."""
    for row, views in enumerate(layout):
        if not views:
            continue
        base = GRID_COLUMNS // len(views)
        for idx, vid in enumerate(views):
            col = idx * base
            span = base if idx < len(views) - 1 else GRID_COLUMNS - col
            yield vid, row, col, span


def is_valid(layout: Layout, view_ids: Sequence[str]) -> bool:
    """True when the layout holds exactly the given views, no empties."""
    if not layout or any(not row for row in layout):
        return False
    if len(view_ids) > MAX_VIEWS:
        return False
    flat = [vid for row in layout for vid in row]
    if len(flat) != len(view_ids):
        return False
    if sorted(flat) != sorted(view_ids):
        return False
    return all(1 <= len(row) <= MAX_VIEWS for row in layout)


def drop_view(layout: Layout, vid: str) -> Layout:
    """Remove a view, deleting rows it leaves empty."""
    rows = [[v for v in row if v != vid] for row in layout]
    return [row for row in rows if row]


def add_view(
    layout: Layout, vid: str, prior_ids: Sequence[str], *, customized: bool = False
) -> Layout:
    """Place a newly added view.

    A user-arranged layout is preserved (insert into the shortest row);
    an untouched default advances to the default for the new count.
    """
    ids = [*prior_ids, vid]
    if not customized or not layout:
        return default_layout(ids)
    rows = [list(row) for row in layout]
    shortest = min(range(len(rows)), key=lambda i: len(rows[i]))
    rows[shortest].append(vid)
    return rows


def remove_view(
    layout: Layout, vid: str, prior_ids: Sequence[str], *, customized: bool = False
) -> Layout:
    """Remove a view; an untouched default falls back to the smaller default."""
    remaining = [v for v in prior_ids if v != vid]
    if not customized:
        return default_layout(remaining)
    return drop_view(layout, vid)


def insert_adjacent(layout: Layout, source: str, target: str, *, after: bool) -> Layout:
    """Move `source` beside `target` in target's row."""
    original = [list(row) for row in layout]
    if not any(source in row for row in layout):
        return original
    rows = drop_view(layout, source)
    for row in rows:
        if target in row:
            row.insert(row.index(target) + (1 if after else 0), source)
            return rows
    return original


def insert_row(layout: Layout, source: str, target: str, *, after: bool) -> Layout:
    """Move `source` into a new row above/below target's row."""
    original = [list(row) for row in layout]
    if not any(source in row for row in layout):
        return original
    rows = drop_view(layout, source)
    for idx, row in enumerate(rows):
        if target in row:
            rows.insert(idx + (1 if after else 0), [source])
            return rows
    return original


def swap(layout: Layout, first: str, second: str) -> Layout:
    """Exchange the grid cells of two views."""
    rows = [list(row) for row in layout]
    if first == second:
        return rows
    pos_first: tuple[int, int] | None = None
    pos_second: tuple[int, int] | None = None
    for row_idx, row in enumerate(rows):
        if first in row:
            pos_first = (row_idx, row.index(first))
        if second in row:
            pos_second = (row_idx, row.index(second))
    if pos_first is None or pos_second is None:
        return rows
    (ra, ca), (rb, cb) = pos_first, pos_second
    rows[ra][ca], rows[rb][cb] = rows[rb][cb], rows[ra][ca]
    return rows


def apply_zone(layout: Layout, source: str, target: str, zone: str) -> Layout:
    """Apply one drop gesture (edge inserts, center swaps)."""
    if source == target:
        return [list(row) for row in layout]
    if zone == ZONE_LEFT:
        return insert_adjacent(layout, source, target, after=False)
    if zone == ZONE_RIGHT:
        return insert_adjacent(layout, source, target, after=True)
    if zone == ZONE_TOP:
        return insert_row(layout, source, target, after=False)
    if zone == ZONE_BOTTOM:
        return insert_row(layout, source, target, after=True)
    if zone == ZONE_CENTER:
        return swap(layout, source, target)
    return [list(row) for row in layout]


def zone_at(x: float, y: float, width: float, height: float) -> str:
    """Drop zone for a pointer position inside a pane.

    Edge bands cover 25% of each side; the remaining center swaps. The
    nearest band wins in a corner, where two bands overlap.
    """
    if width <= 0 or height <= 0:
        return ZONE_CENTER
    fx = x / width
    fy = y / height
    candidates: list[tuple[str, float]] = []
    if fx < 0.25:
        candidates.append((ZONE_LEFT, fx))
    if fx > 0.75:
        candidates.append((ZONE_RIGHT, 1.0 - fx))
    if fy < 0.25:
        candidates.append((ZONE_TOP, fy))
    if fy > 0.75:
        candidates.append((ZONE_BOTTOM, 1.0 - fy))
    if not candidates:
        return ZONE_CENTER
    return min(candidates, key=lambda item: item[1])[0]
