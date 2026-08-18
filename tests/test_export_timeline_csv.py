"""Tests for export_timeline_csv.py's pure merge logic. Zero network calls
- world/message/status entries are hand-built. The CSV-writing and MCP
fetch aren't unit-tested here (no browser/network runtime in this repo's
test stack) - verified manually against a live episode. See D29.
"""

import sys

sys.path.insert(0, ".")

from export_timeline_csv import build_timeline_rows  # noqa: E402


def test_merges_and_sorts_all_three_sources_by_real_timestamp():
    world_entries = [
        {"timestamp": 100.0, "side": "a", "action": "move", "resolved": "move", "a_position": 2, "b_position": 8},
        {"timestamp": 102.0, "side": "b", "action": "wait", "resolved": "wait", "a_position": 2, "b_position": 8},
    ]
    messages = [
        {"timestamp": 101.0, "speaker": "Robot A", "intent": "propose", "goes_first": "Robot A", "text": "go"},
    ]
    status_entries = [
        {"timestamp": 100.5, "side": "a", "detail": "at boundary, sensing Robot B - dialing"},
    ]

    rows = build_timeline_rows(world_entries, messages, status_entries)

    assert [row["event_type"] for row in rows] == ["world", "status", "message", "world"]
    assert [row["timestamp"] for row in rows] == [100.0, 100.5, 101.0, 102.0]


def test_elapsed_s_is_relative_to_the_first_row():
    world_entries = [
        {"timestamp": 50.0, "side": "a", "action": "move", "resolved": "move", "a_position": 2, "b_position": 8},
        {"timestamp": 51.5, "side": "b", "action": "move", "resolved": "move", "a_position": 2, "b_position": 7},
    ]

    rows = build_timeline_rows(world_entries, [], [])

    assert rows[0]["elapsed_s"] == 0.0
    assert rows[1]["elapsed_s"] == 1.5


def test_each_row_only_fills_columns_that_belong_to_its_own_event_type():
    world_entries = [
        {"timestamp": 1.0, "side": "a", "action": "move", "resolved": "move", "a_position": 2, "b_position": 8}
    ]
    messages = [{"timestamp": 2.0, "speaker": "Robot A", "intent": "accept", "goes_first": "Robot B", "text": "fine"}]
    status_entries = [{"timestamp": 3.0, "side": "b", "detail": "reached target"}]

    rows = build_timeline_rows(world_entries, messages, status_entries)

    world_row, message_row, status_row = rows
    assert world_row["world_action"] == "move" and world_row["message_text"] == "" and world_row["status_detail"] == ""
    assert message_row["message_text"] == "fine" and message_row["world_action"] == "" and message_row["status_detail"] == ""
    assert status_row["status_detail"] == "reached target" and status_row["world_action"] == "" and status_row["message_text"] == ""


def test_empty_inputs_produce_no_rows():
    assert build_timeline_rows([], [], []) == []


def test_grid_adds_a_synthetic_start_row_before_the_first_real_event():
    world_entries = [
        {"timestamp": 100.0, "side": "b", "action": "move", "resolved": "move", "a_position": 1, "b_position": 7},
        {"timestamp": 101.0, "side": "a", "action": "move", "resolved": "move", "a_position": 2, "b_position": 7},
    ]
    grid = {"min_position": 1, "max_position": 8}

    rows = build_timeline_rows(world_entries, [], [], grid)

    assert len(rows) == 3
    start_row = rows[0]
    assert start_row["event_type"] == "start"
    assert start_row["a_position"] == 1
    assert start_row["b_position"] == 8
    assert start_row["timestamp"] < world_entries[0]["timestamp"]
    assert start_row["elapsed_s"] == 0.0  # the synthetic row now anchors elapsed_s == 0
    assert rows[1]["elapsed_s"] > 0.0  # the first real event is fractionally after it, not simultaneous


def test_no_grid_means_no_synthetic_start_row():
    world_entries = [
        {"timestamp": 100.0, "side": "b", "action": "move", "resolved": "move", "a_position": 1, "b_position": 7}
    ]
    rows = build_timeline_rows(world_entries, [], [])
    assert len(rows) == 1
    assert rows[0]["event_type"] == "world"


def test_grid_with_no_world_entries_adds_nothing():
    assert build_timeline_rows([], [], [], {"min_position": 1, "max_position": 8}) == []
