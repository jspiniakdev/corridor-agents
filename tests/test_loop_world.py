"""Tests for Phase 11a step 1: docs/PHASE_11_ROADMAP.md's loop geometry and
reactive safety layer (src/loop_world.py). Zero API calls, no policies
involved at all - like world.py's own reactive tests, this layer is pure
arithmetic, and it's the safety net that must hold even if everything above
it (negotiation, life/urgency, an LLM) is wrong. See docs/DECISIONS.md D48.
"""

import sys

sys.path.insert(0, "src")

from loop_world import (  # noqa: E402
    CORRIDORS,
    PERIMETER,
    RobotState,
    WorldState,
    apply,
    arc_gap,
    ccw_boundary,
    corridor_containing,
    cw_boundary,
    forward_distance,
    in_span,
    next_corridor,
    reactive_filter,
)


def make_state(**robots: tuple[int, int, str]) -> WorldState:
    """robots: name -> (position, direction, corner)."""
    return WorldState({
        name: RobotState(name=name, position=pos, direction=direction, corner=corner)
        for name, (pos, direction, corner) in robots.items()
    })


# --- geometry -----------------------------------------------------------


def test_corridor_boundaries_match_the_settled_geometry():
    # D48: revised from an original [8,13]/[25,28] draft that broke
    # negotiation on the flagship duel scenario - see the module docstring.
    assert cw_boundary("north") == 5
    assert ccw_boundary("north") == 12
    assert cw_boundary("south") == 26
    assert ccw_boundary("south") == 31


def test_in_span_and_corridor_containing():
    for p in range(6, 12):
        assert in_span(p, "north") is True
        assert corridor_containing(p) == "north"
    for p in range(27, 31):
        assert in_span(p, "south") is True
        assert corridor_containing(p) == "south"
    for p in [0, 5, 12, 16, 22, 26, 31, 38, 43]:
        assert corridor_containing(p) is None


def test_forward_distance_wraps_around_the_loop():
    assert forward_distance(0, 5, direction=1) == 5
    assert forward_distance(40, 5, direction=1) == 9  # 40->43, wrap to 0->5
    assert forward_distance(5, 0, direction=1) == PERIMETER - 5
    assert forward_distance(5, 0, direction=-1) == 5
    assert forward_distance(2, 40, direction=-1) == 6  # 2->0, wrap to 43->40


def test_arc_gap_is_symmetric_and_shortest():
    assert arc_gap(0, 5) == 5
    assert arc_gap(5, 0) == 5
    assert arc_gap(0, 40) == 4  # short way around the wrap, not 40
    assert arc_gap(10, 10) == 0


def test_next_corridor_picks_the_nearer_one():
    # CW robot just past South, heading toward North the long way around
    assert next_corridor(31, direction=1) == ("north", forward_distance(31, 5, 1))
    # CW robot approaching North directly
    name, dist = next_corridor(0, direction=1)
    assert name == "north" and dist == 5
    # CCW robot approaching North from the TR side
    name, dist = next_corridor(16, direction=-1)
    assert name == "north" and dist == 4


def test_the_settled_geometry_senses_across_both_corridors():
    """D48's actual verification, as a permanent regression test: with the
    settled geometry, whichever robot reaches its boundary first is
    already within SENSOR_RANGE of the other, for the duel config's
    spawn points (TL/CW vs TR/CCW meeting at North, BR/CW vs BL/CCW
    meeting at South)."""
    # North: R2 (TR=16, CCW) reaches its boundary (12) in 4 ticks; R1
    # (TL=0, CW) is at position 4 by then.
    assert forward_distance(16, ccw_boundary("north"), -1) == 4
    r1_position_at_tick_4 = 4
    assert arc_gap(ccw_boundary("north"), r1_position_at_tick_4) <= 10
    # South: CW robot (BR=22) reaches its boundary (26) in 4 ticks; CCW
    # robot (BL=38) is at position 34 by then.
    assert forward_distance(22, cw_boundary("south"), 1) == 4
    ccw_position_at_tick_4 = 34
    assert arc_gap(cw_boundary("south"), ccw_position_at_tick_4) <= 10


# --- RobotState properties -----------------------------------------------


def test_at_boundary_true_only_exactly_at_the_stop_and_wait_cell():
    cw = RobotState(name="R", position=5, direction=1, corner="TL")
    assert cw.at_boundary is True
    assert cw.in_zone is False

    ccw = RobotState(name="R", position=12, direction=-1, corner="TR")
    assert ccw.at_boundary is True

    not_yet = RobotState(name="R", position=4, direction=1, corner="TL")
    assert not_yet.at_boundary is False


def test_in_zone_true_inside_the_span_regardless_of_direction():
    inside_cw = RobotState(name="R", position=9, direction=1, corner="TL")
    inside_ccw = RobotState(name="R", position=9, direction=-1, corner="TR")
    assert inside_cw.in_zone is True
    assert inside_ccw.in_zone is True
    assert inside_cw.at_boundary is False  # in_zone short-circuits at_boundary


def test_lane_is_exactly_direction():
    cw = RobotState(name="R", position=0, direction=1, corner="TL")
    ccw = RobotState(name="R", position=16, direction=-1, corner="TR")
    assert cw.lane == 1
    assert ccw.lane == -1


def test_approaching_corridor_reports_current_when_in_zone_else_next():
    inside = RobotState(name="R", position=28, direction=1, corner="BR")
    assert inside.approaching_corridor == "south"

    approaching = RobotState(name="R", position=0, direction=1, corner="TL")
    assert approaching.approaching_corridor == "north"


# --- reactive_filter: corridor entry safety ------------------------------


def test_lone_entrant_is_allowed_through():
    state = make_state(R1=(5, 1, "TL"))  # at North's CW boundary, corridor empty
    resolved = reactive_filter(state, {"R1": "move"})
    assert resolved["R1"] == "move"


def test_second_entrant_is_blocked_while_the_corridor_is_occupied():
    state = make_state(
        R1=(9, 1, "TL"),   # already inside North
        R2=(12, -1, "TR"),  # at North's CCW boundary, about to enter
    )
    resolved = reactive_filter(state, {"R1": "move", "R2": "move"})
    assert resolved["R1"] == "move"   # continuing through: always safe
    assert resolved["R2"] == "wait"   # corridor occupied: blocked


def test_simultaneous_entry_from_both_ends_blocks_both():
    state = make_state(
        R1=(5, 1, "TL"),    # North CW boundary, about to enter
        R2=(12, -1, "TR"),  # North CCW boundary, about to enter
    )
    resolved = reactive_filter(state, {"R1": "move", "R2": "move"})
    assert resolved["R1"] == "wait"
    assert resolved["R2"] == "wait"


def test_swap_through_the_bridge_is_blocked():
    """A robot entering a corridor from one end while another exits from
    the other end, in the same tick, must not be allowed to pass through
    each other - the corridor is one lane. The exiting robot's own move
    is unaffected; it already committed."""
    state = make_state(
        R1=(5, 1, "TL"),   # about to enter North
        R2=(6, -1, "TR"),  # already inside North (at its edge cell), about to exit
    )
    resolved = reactive_filter(state, {"R1": "move", "R2": "move"})
    assert resolved["R1"] == "wait"   # corridor reads as occupied at tick start
    assert resolved["R2"] == "move"   # exiting is never blocked


def test_two_different_corridors_dont_cross_block():
    state = make_state(
        R1=(5, 1, "TL"),    # entering North
        R2=(26, 1, "BR"),   # entering South
    )
    resolved = reactive_filter(state, {"R1": "move", "R2": "move"})
    assert resolved["R1"] == "move"
    assert resolved["R2"] == "move"


def test_waiting_robot_stays_put_through_apply():
    state = make_state(R1=(9, 1, "TL"))
    resolved = reactive_filter(state, {"R1": "wait"})
    assert resolved["R1"] == "wait"
    apply(state, resolved)
    assert state.robots["R1"].position == 9


# --- reactive_filter: same-lane queueing (11b territory, tested now) -----


def test_queueing_behind_a_stationary_same_lane_robot_is_blocked():
    # positions 1-3: plain 2-lane top-left section, well outside both
    # corridor spans (north 6-11, south 27-30)
    state = make_state(
        R1=(2, 1, "TL"),  # holding
        R2=(1, 1, "TL"),  # right behind R1, same lane
    )
    resolved = reactive_filter(state, {"R1": "wait", "R2": "move"})
    assert resolved["R2"] == "wait"


def test_queueing_cascades_through_a_chain():
    state = make_state(
        R1=(3, 1, "TL"),  # holding (e.g. blocked at a corridor, further ahead)
        R2=(2, 1, "TL"),
        R3=(1, 1, "TL"),
    )
    resolved = reactive_filter(state, {"R1": "wait", "R2": "move", "R3": "move"})
    assert resolved["R1"] == "wait"
    assert resolved["R2"] == "wait"  # would collide with R1
    assert resolved["R3"] == "wait"  # would collide with R2, once R2 is held


def test_flowing_same_lane_traffic_is_not_falsely_blocked():
    """The trailing robot moving into the leading robot's *current* cell
    is fine as long as the leading robot is also genuinely moving out of
    it this tick - only a real collision (equal final positions) blocks."""
    state = make_state(
        R1=(2, 1, "TL"),
        R2=(1, 1, "TL"),
    )
    resolved = reactive_filter(state, {"R1": "move", "R2": "move"})
    assert resolved["R1"] == "move"
    assert resolved["R2"] == "move"


def test_opposite_lanes_never_queue_behind_each_other():
    state = make_state(
        R1=(2, 1, "TL"),   # CW, holding
        R2=(3, -1, "TR"),  # CCW, would move into R1's cell - different lane
    )
    resolved = reactive_filter(state, {"R1": "wait", "R2": "move"})
    assert resolved["R2"] == "move"


# --- apply ----------------------------------------------------------------


def test_apply_moves_only_resolved_movers():
    state = make_state(R1=(0, 1, "TL"), R2=(16, -1, "TR"))
    apply(state, {"R1": "move", "R2": "wait"})
    assert state.robots["R1"].position == 1
    assert state.robots["R2"].position == 16


def test_apply_wraps_around_the_loop():
    state = make_state(R1=(PERIMETER - 1, 1, "TL"), R2=(0, -1, "TL"))
    apply(state, {"R1": "move", "R2": "move"})
    assert state.robots["R1"].position == 0
    assert state.robots["R2"].position == PERIMETER - 1


# --- the load-bearing structural guarantee --------------------------------


def test_naive_always_move_robots_never_collide_over_many_ticks():
    """Even with zero coordination - every robot always attempts to move,
    every tick, no negotiation, no yielding - the reactive layer alone
    must guarantee no two robots ever share a (lane, position) cell, and
    no corridor ever holds more than one robot at once. Mirrors world.py's
    test_naive_always_move_robots_never_collide, generalized to N robots,
    two directions, two corridors."""
    state = make_state(
        R1=(0, 1, "TL"),
        R2=(16, -1, "TR"),
        R3=(22, 1, "BR"),
        R4=(38, -1, "BL"),
    )
    for _ in range(300):
        proposed = {name: "move" for name in state.robots}
        resolved = reactive_filter(state, proposed)
        apply(state, resolved)

        # invariant 1: at most one robot per (lane, position)
        seen = {}
        for r in state.robots.values():
            key = (r.lane, r.position)
            assert key not in seen, f"collision at {key}: {seen.get(key)} and {r.name}"
            seen[key] = r.name

        # invariant 2: at most one robot per corridor
        for corridor in CORRIDORS:
            occupants = [r.name for r in state.robots.values() if r.in_zone and r.current_corridor == corridor]
            assert len(occupants) <= 1, f"{corridor} has multiple occupants: {occupants}"
