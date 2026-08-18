"""Tests for world_server.py's tools. These are plain Python functions even
after @mcp.tool() - no real MCP server or network needed to test the logic,
same "test the decision, not the transport" split used throughout this
project. Real client/server wiring is manually verified instead (see D17).
"""

import sys

sys.path.insert(0, ".")
sys.path.insert(0, "src")

from world import B_START  # noqa: E402

import world_server as ws  # noqa: E402


def reset_state():
    ws.STATE = ws.build_state()


def test_get_map_matches_world_constants():
    reset_state()
    result = ws.get_map()
    assert result["corridor_zone"] == [3, 4, 5]
    assert result["a_boundary"] == 2
    assert result["b_boundary"] == 6


def test_get_observation_reflects_current_position():
    reset_state()
    obs = ws.get_observation("a")
    assert obs["position"] == 1
    assert obs["at_boundary"] is False
    assert obs["sensed_other"] is False  # A=1, B=8, gap=7 > SENSOR_RANGE=6 (D25)
    assert obs["other_distance_to_boundary"] is None  # not sensed, so not knowable


def test_get_observation_senses_other_within_range():
    reset_state()
    ws.STATE.a.position = 3
    ws.STATE.b.position = 5
    obs = ws.get_observation("a")
    assert obs["sensed_other"] is True
    assert obs["gap_if_sensed"] == 2


def test_get_observation_reports_other_distance_to_its_own_boundary():
    reset_state()
    ws.STATE.a.position = 1
    ws.STATE.b.position = 5  # sensed (gap=4 <= 6), 1 step from its own boundary (6)
    obs = ws.get_observation("a")
    assert obs["sensed_other"] is True
    assert obs["other_distance_to_boundary"] == 1


def test_propose_action_rejects_invalid_action():
    reset_state()
    try:
        ws.propose_action("a", "sprint")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_propose_action_moves_when_safe():
    reset_state()
    result = ws.propose_action("a", "move")
    assert result == {"accepted": True, "actual_position": 2}
    assert ws.STATE.a.position == 2


def test_propose_action_wait_never_changes_position():
    reset_state()
    result = ws.propose_action("a", "wait")
    assert result == {"accepted": True, "actual_position": 1}


def test_propose_action_blocks_entry_while_the_other_robot_is_in_the_zone():
    reset_state()
    ws.STATE.a.position = 4  # already inside the corridor zone
    ws.STATE.b.position = 6  # at its boundary, about to try entering

    result = ws.propose_action("b", "move")

    assert result["accepted"] is False
    assert result["actual_position"] == 6  # held at the boundary
    assert ws.STATE.b.position == 6


def test_propose_action_only_moves_the_requesting_side():
    reset_state()
    ws.propose_action("a", "move")
    assert ws.STATE.b.position == B_START  # untouched by A's call


def test_get_log_starts_empty():
    reset_state()
    assert ws.get_log() == {"entries": []}


def test_get_log_records_each_committed_action_in_order():
    reset_state()
    ws.propose_action("a", "move")
    ws.propose_action("b", "wait")

    entries = ws.get_log()["entries"]

    assert len(entries) == 2
    for entry in entries:
        assert isinstance(entry.pop("timestamp"), float)  # real wall clock (D20) - checked separately, not for equality
    assert entries[0] == {"side": "a", "action": "move", "resolved": "move", "a_position": 2, "b_position": B_START}
    assert entries[1] == {"side": "b", "action": "wait", "resolved": "wait", "a_position": 2, "b_position": B_START}


def test_get_log_timestamps_are_monotonically_non_decreasing():
    reset_state()
    ws.propose_action("a", "move")
    ws.propose_action("b", "wait")
    ws.propose_action("a", "move")

    timestamps = [entry["timestamp"] for entry in ws.get_log()["entries"]]

    assert timestamps == sorted(timestamps)
