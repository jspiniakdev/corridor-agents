"""Tests for the Phase 3 reactive layer. Zero API calls, no policies involved
at all - this layer is pure grid arithmetic, and it's the safety net that
must hold even if everything above it is wrong.
"""

import sys

sys.path.insert(0, "src")

from negotiation import Robot  # noqa: E402
from world import (  # noqa: E402
    A_BOUNDARY,
    A_DIRECTION,
    A_START,
    A_TARGET,
    B_BOUNDARY,
    B_DIRECTION,
    B_START,
    B_TARGET,
    CORRIDOR_ZONE,
    RobotState,
    WorldState,
    apply,
    reactive_filter,
)


def make_state(a_position, b_position):
    a = RobotState(Robot("Robot A", "", 0, None), a_position, A_DIRECTION, A_BOUNDARY, A_TARGET)
    b = RobotState(Robot("Robot B", "", 0, None), b_position, B_DIRECTION, B_BOUNDARY, B_TARGET)
    return WorldState(a, b)


def test_lone_entrant_is_allowed_through():
    state = make_state(a_position=2, b_position=14)  # B is far away
    a_action, b_action = reactive_filter(state, "move", "move")
    assert a_action == "move"
    assert b_action == "move"


def test_second_entrant_is_blocked_while_the_zone_is_occupied():
    state = make_state(a_position=8, b_position=10)  # A is already in the zone
    a_action, b_action = reactive_filter(state, "move", "move")
    assert a_action == "move"  # A may continue through
    assert b_action == "wait"  # B must not enter while A is inside


def test_simultaneous_entry_attempt_blocks_both():
    state = make_state(a_position=6, b_position=10)  # neither is in the zone yet
    a_action, b_action = reactive_filter(state, "move", "move")
    assert a_action == "wait"
    assert b_action == "wait"


def test_swap_through_the_bridge_is_blocked():
    """A regression test for a real gap: A entering the zone from one end
    while B exits from the other end, in the same tick, used to be allowed
    (neither robot's next position alone was 'both in the zone', so the old
    check missed it) - passing them through each other on the one-lane
    bridge. A must wait; B's exit isn't blocked, since leaving the bridge
    doesn't need to check anything."""
    state = make_state(a_position=6, b_position=7)  # A about to enter, B about to exit
    a_action, b_action = reactive_filter(state, "move", "move")
    assert a_action == "wait"
    assert b_action == "move"


def test_naive_always_move_robots_never_collide():
    """Even with zero coordination - both robots always attempt to move -
    the reactive layer alone must guarantee they're never both in the zone
    at the same time. They'll starve at the boundary instead."""
    state = make_state(a_position=A_START, b_position=B_START)
    for _ in range(30):
        a_action, b_action = reactive_filter(state, "move", "move")
        apply(state, a_action, b_action)
        assert not (state.a.position in CORRIDOR_ZONE and state.b.position in CORRIDOR_ZONE)
