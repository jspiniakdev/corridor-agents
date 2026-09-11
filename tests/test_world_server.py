"""Tests for world_server.py's tools. These are plain Python functions even
after @mcp.tool() - no real MCP server or network needed to test the logic,
same "test the decision, not the transport" split used throughout this
project. Real client/server wiring is manually verified instead (see D17).

Since D38 the tools go through world_server._store (an InMemoryWorldStore
by default); tests reach into it via _state() rather than a module STATE.
FirestoreWorldStore's transaction wiring is verified live, not here.
"""

import sys

sys.path.insert(0, ".")
sys.path.insert(0, "src")

from world import B_START  # noqa: E402

import world_server as ws  # noqa: E402


def reset_state():
    ws._store.reset()


def _state():
    return ws._store.get_state()


def test_get_map_matches_world_constants():
    reset_state()
    result = ws.get_map()
    assert result["corridor_zone"] == [7, 8, 9]
    assert result["a_boundary"] == 6
    assert result["b_boundary"] == 10


def test_get_observation_reflects_current_position():
    reset_state()
    obs = ws.get_observation("a")
    assert obs["position"] == 1
    assert obs["at_boundary"] is False
    assert obs["sensed_other"] is False  # A=1, B=14, gap=13 > SENSOR_RANGE=6 (D25)
    assert obs["other_distance_to_boundary"] is None  # not sensed, so not knowable


def test_get_observation_senses_other_within_range():
    reset_state()
    _state().a.position = 3
    _state().b.position = 5
    obs = ws.get_observation("a")
    assert obs["sensed_other"] is True
    assert obs["gap_if_sensed"] == 2


def test_get_observation_reports_other_distance_to_its_own_boundary():
    reset_state()
    _state().a.position = 6  # A at its boundary
    _state().b.position = 9  # sensed (gap=3 <= 6), 1 step from its own boundary (10)
    obs = ws.get_observation("a")
    assert obs["sensed_other"] is True
    assert obs["other_distance_to_boundary"] == 1


def test_get_observation_reports_other_distance_to_the_corridor_entrance():
    reset_state()
    _state().a.position = 6
    _state().b.position = 10  # the reviewed standoff - both one cell from the corridor
    a_obs = ws.get_observation("a")
    assert a_obs["distance_to_entrance"] == 1
    assert a_obs["other_distance_to_entrance"] == 1  # B at 10 -> entrance 9
    b_obs = ws.get_observation("b")
    assert b_obs["distance_to_entrance"] == 1
    assert b_obs["other_distance_to_entrance"] == 1  # A at 6 -> entrance 7


def test_get_observation_other_distance_to_entrance_none_when_not_sensed():
    reset_state()  # A=1, B=14, gap 13 > sensor 6
    assert ws.get_observation("a")["other_distance_to_entrance"] is None


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
    assert _state().a.position == 2


def test_propose_action_wait_never_changes_position():
    reset_state()
    result = ws.propose_action("a", "wait")
    assert result == {"accepted": True, "actual_position": 1}


def test_propose_action_blocks_entry_while_the_other_robot_is_in_the_zone():
    reset_state()
    _state().a.position = 8  # already inside the corridor zone
    _state().b.position = 10  # at its boundary, about to try entering

    result = ws.propose_action("b", "move")

    assert result["accepted"] is False
    assert result["actual_position"] == 10  # held at the boundary
    assert _state().b.position == 10


def test_propose_action_only_moves_the_requesting_side():
    reset_state()
    ws.propose_action("a", "move")
    assert _state().b.position == B_START  # untouched by A's call


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


def test_reset_world_returns_both_robots_to_start_with_an_empty_log():
    reset_state()
    ws.propose_action("a", "move")  # each side's first move - not throttled (D43)
    ws.propose_action("b", "move")
    assert (_state().a.position, _state().b.position) == (2, B_START - 1)

    assert ws.reset_world() == {"reset": True}

    assert _state().a.position == 1
    assert _state().b.position == B_START
    assert ws.get_log() == {"entries": []}


def test_get_observation_carries_the_episode_id():
    reset_state()
    ep = ws.get_observation("a")["episode_id"]
    assert isinstance(ep, str) and ep
    assert ws.get_observation("b")["episode_id"] == ep  # same episode for both sides


def test_propose_action_stale_episode_is_refused():
    reset_state()
    result = ws.propose_action("a", "move", episode_id="a-finished-episode")
    assert result == {"accepted": False, "actual_position": 1, "reason": "stale_episode"}
    assert _state().a.position == 1


def test_propose_action_nonce_makes_a_resend_idempotent():
    reset_state()
    ep = ws.get_observation("a")["episode_id"]
    first = ws.propose_action("a", "move", episode_id=ep, nonce="abc")
    assert first == {"accepted": True, "actual_position": 2}
    resend = ws.propose_action("a", "move", episode_id=ep, nonce="abc")
    assert resend == first  # same outcome
    assert _state().a.position == 2  # not 3


def test_propose_action_too_fast_move_is_refused_with_a_reason():
    """D43: a second 'move' from the same side before the world's minimum
    interval comes back accepted=False, reason='too_fast', position held -
    distinct from a safety block (no reason)."""
    from world_store import InMemoryWorldStore

    clock = [100.0]
    ws._store = InMemoryWorldStore(min_move_interval=1.0, now=lambda: clock[0])
    try:
        assert ws.propose_action("a", "move") == {"accepted": True, "actual_position": 2}
        clock[0] += 0.2
        assert ws.propose_action("a", "move") == {
            "accepted": False,
            "actual_position": 2,
            "reason": "too_fast",
        }
    finally:
        ws._store = InMemoryWorldStore()


def test_record_and_get_negotiation_round_trip():
    reset_state()
    assert ws.get_negotiation() == {"negotiation": None}

    msgs = [{"speaker": "Robot A", "intent": "propose", "goes_first": "Robot A", "text": "me first", "timestamp": 1.0}]
    assert ws.record_negotiation(msgs, 0.5, 2.0) == {"recorded": 1, "stale": False}

    stored = ws.get_negotiation()["negotiation"]
    assert stored["messages"] == msgs
    assert stored["comms_established_at"] == 0.5
    assert isinstance(stored["resolved_at"], float)  # D44: re-stamped with the world's clock, not the passed 2.0

    ws.reset_world()
    assert ws.get_negotiation() == {"negotiation": None}


def test_record_negotiation_from_a_stale_episode_is_refused():
    """D44: a transcript tagged with a finished episode's id doesn't
    clobber world/current - the livelocked-episode-1 bug."""
    reset_state()
    current = ws.get_observation("a")["episode_id"]
    msgs = [{"speaker": "Robot A", "intent": "propose", "goes_first": "Robot A", "text": "x", "timestamp": 1.0}]

    assert ws.record_negotiation(msgs, 0.5, 2.0, episode_id="not-the-current-one") == {"recorded": 0, "stale": True}
    assert ws.get_negotiation() == {"negotiation": None}  # untouched

    assert ws.record_negotiation(msgs, 0.5, 2.0, episode_id=current)["recorded"] == 1
    assert ws.get_negotiation()["negotiation"]["messages"] == msgs
