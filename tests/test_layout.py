"""Pure tests for the pane layout model (no Qt needed)."""

from __future__ import annotations

from vcplayer.ui import layout as pane_layout


def test_default_layouts() -> None:
    assert pane_layout.default_layout(["a", "b"]) == [["a", "b"]]
    assert pane_layout.default_layout(["a", "b", "c"]) == [["a", "b", "c"]]
    assert pane_layout.default_layout(["a", "b", "c", "d"]) == [
        ["a", "b"],
        ["c", "d"],
    ]
    assert pane_layout.default_layout([]) == []


def test_cells_cover_grid_rows() -> None:
    layouts = [
        [["a"]],
        [["a", "b"]],
        [["a", "b", "c"]],
        [["a", "b", "c", "d"]],
        [["a", "b"], ["c", "d"]],
        [["a"], ["b"]],
        [["a", "b", "c"], ["d"]],
    ]
    for layout in layouts:
        placed = list(pane_layout.cells(layout))
        assert len(placed) == sum(len(row) for row in layout)
        for row_idx, _row in enumerate(layout):
            row_cells = [cell for cell in placed if cell[1] == row_idx]
            assert row_cells[0][2] == 0
            end = 0
            for _vid, _r, col, span in row_cells:
                assert col == end
                end = col + span
            assert end == pane_layout.GRID_COLUMNS


def test_zone_at_edges_and_center() -> None:
    width, height = 200.0, 100.0
    assert pane_layout.zone_at(10, 50, width, height) == pane_layout.ZONE_LEFT
    assert pane_layout.zone_at(190, 50, width, height) == pane_layout.ZONE_RIGHT
    assert pane_layout.zone_at(100, 10, width, height) == pane_layout.ZONE_TOP
    assert pane_layout.zone_at(100, 90, width, height) == pane_layout.ZONE_BOTTOM
    assert pane_layout.zone_at(100, 50, width, height) == pane_layout.ZONE_CENTER
    # Corner: the nearer band wins.
    assert pane_layout.zone_at(2, 40, width, height) == pane_layout.ZONE_LEFT
    assert pane_layout.zone_at(100, 50, 0, 0) == pane_layout.ZONE_CENTER


def test_add_view_keeps_custom_layout() -> None:
    custom = [["a", "b"], ["c"]]
    assert pane_layout.add_view(custom, "d", ["a", "b", "c"], customized=True) == [
        ["a", "b"],
        ["c", "d"],
    ]


def test_custom_layout_survives_add_remove_roundtrip() -> None:
    top2 = [["a", "b"], ["c"]]
    added = pane_layout.add_view(top2, "d", ["a", "b", "c"], customized=True)
    assert added == [["a", "b"], ["c", "d"]]
    assert pane_layout.remove_view(added, "d", ["a", "b", "c", "d"], customized=True) == top2


def test_add_view_follows_default_progression() -> None:
    assert pane_layout.add_view([["a", "b"]], "c", ["a", "b"]) == [["a", "b", "c"]]
    assert pane_layout.add_view([["a", "b", "c"]], "d", ["a", "b", "c"]) == [
        ["a", "b"],
        ["c", "d"],
    ]


def test_remove_view_follows_default_progression() -> None:
    assert pane_layout.remove_view([["a", "b"], ["c", "d"]], "d", ["a", "b", "c", "d"]) == [
        ["a", "b", "c"]
    ]
    custom = [["a", "b", "c"], ["d"]]
    assert pane_layout.remove_view(custom, "d", ["a", "b", "c", "d"], customized=True) == [
        ["a", "b", "c"]
    ]


def test_insert_adjacent_same_row() -> None:
    assert pane_layout.insert_adjacent([["a", "b", "c"]], "a", "b", after=True) == [["b", "a", "c"]]
    assert pane_layout.insert_adjacent([["a", "b", "c"]], "c", "a", after=False) == [
        ["c", "a", "b"]
    ]


def test_insert_adjacent_other_row() -> None:
    assert pane_layout.insert_adjacent([["a", "b"], ["c", "d"]], "d", "a", after=False) == [
        ["d", "a", "b"],
        ["c"],
    ]


def test_insert_row_above_and_below() -> None:
    assert pane_layout.insert_row([["a", "b"], ["c", "d"]], "d", "a", after=False) == [
        ["d"],
        ["a", "b"],
        ["c"],
    ]
    assert pane_layout.insert_row([["a", "b"], ["c", "d"]], "a", "c", after=True) == [
        ["b"],
        ["c", "d"],
        ["a"],
    ]


def test_swap() -> None:
    assert pane_layout.swap([["a", "b"], ["c", "d"]], "a", "d") == [
        ["d", "b"],
        ["c", "a"],
    ]
    assert pane_layout.swap([["a", "b"]], "a", "a") == [["a", "b"]]


def test_is_valid() -> None:
    assert pane_layout.is_valid([["a", "b"]], ["a", "b"])
    assert not pane_layout.is_valid([["a"]], ["a", "b"])
    assert not pane_layout.is_valid([["a", "b"], []], ["a", "b"])
    assert not pane_layout.is_valid([["a", "a"]], ["a", "b"])


def test_presets_are_valid() -> None:
    for ids in (["a", "b"], ["a", "b", "c"], ["a", "b", "c", "d"]):
        presets = pane_layout.presets(ids)
        assert presets
        for _label, preset in presets:
            assert pane_layout.is_valid(preset, ids)


def test_apply_zone_and_noop() -> None:
    layout = [["a", "b"], ["c", "d"]]
    assert pane_layout.apply_zone(layout, "d", "a", pane_layout.ZONE_TOP) == [
        ["d"],
        ["a", "b"],
        ["c"],
    ]
    assert pane_layout.apply_zone(layout, "a", "a", pane_layout.ZONE_RIGHT) == layout
    assert pane_layout.apply_zone(layout, "a", "b", "bogus") == layout


def test_insert_unknown_source_is_noop() -> None:
    layout = [["a", "b"]]
    assert pane_layout.insert_adjacent(layout, "x", "a", after=True) == layout
    assert pane_layout.insert_row(layout, "x", "a", after=False) == layout


def test_is_valid_rejects_more_than_max_views() -> None:
    ids = ["a", "b", "c", "d", "e"]
    assert not pane_layout.is_valid([["a", "b", "c", "d"], ["e"]], ids)
