"""Phase 10b-2/D38: world_store.py. Everything that decides an outcome is
pure and tested here; FirestoreWorldStore's transaction wiring is verified
live against the emulator, not in the unit suite (it needs a real Firestore).
"""

import sys

sys.path.insert(0, ".")
sys.path.insert(0, "src")

from world import A_START, B_START, RobotState, WorldState, apply, reactive_filter  # noqa: E402

from world_store import InMemoryWorldStore, _resolve, state_from_positions  # noqa: E402


def test_state_from_positions_rebuilds_a_usable_worldstate():
    state = state_from_positions(3, 5)
    assert (state.a.position, state.b.position) == (3, 5)
    assert state.a.direction == 1 and state.b.direction == -1
    assert state.a.in_zone is True  # 3 is in CORRIDOR_ZONE {3,4,5}
    assert state.log == []


def test_resolve_applies_a_safe_move_and_returns_the_log_entry():
    state = state_from_positions(A_START, B_START)
    entry = _resolve(state, "a", "move")
    assert entry == {
        "side": "a",
        "action": "move",
        "resolved": "move",
        "a_position": 2,
        "b_position": B_START,
        "timestamp": entry["timestamp"],
    }
    assert isinstance(entry["timestamp"], float)
    assert state.a.position == 2


def test_resolve_downgrades_an_unsafe_move_to_wait():
    state = state_from_positions(4, 6)  # A already in the zone, B at its boundary
    entry = _resolve(state, "b", "move")
    assert entry["action"] == "move"
    assert entry["resolved"] == "wait"
    assert state.b.position == 6  # held


def test_inmemory_store_matches_raw_world_py_over_a_sequence():
    """The D17 guarantee: routing through the store changes nothing about
    the outcome vs. calling reactive_filter/apply on a world.py WorldState
    directly."""
    store = InMemoryWorldStore()
    ref = WorldState(
        RobotState(store.get_state().a.robot, A_START, 1, 2, 8),
        RobotState(store.get_state().b.robot, B_START, -1, 6, 1),
    )
    moves = [("a", "move"), ("b", "move"), ("a", "move"), ("b", "wait"), ("a", "move")]
    for side, action in moves:
        store.propose(side, action)
        if side == "a":
            r, _ = reactive_filter(ref, action, "wait")
            apply(ref, r, "wait")
        else:
            _, r = reactive_filter(ref, "wait", action)
            apply(ref, "wait", r)
        assert (store.get_state().a.position, store.get_state().b.position) == (ref.a.position, ref.b.position)


def test_inmemory_store_log_and_reset():
    store = InMemoryWorldStore()
    store.propose("a", "move")
    store.propose("a", "move")
    assert len(store.get_log()) == 2
    assert store.get_state().a.position == 3

    store.reset()
    assert store.get_state().a.position == A_START
    assert store.get_state().b.position == B_START
    assert store.get_log() == []
