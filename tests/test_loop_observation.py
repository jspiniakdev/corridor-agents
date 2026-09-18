"""Tests for Phase 11a step 4: loop_observation.py's observation
composition. Zero API calls, no policies - pure text-composition tests
against directly-built WorldState fixtures. See docs/DECISIONS.md D48.
"""

import sys

sys.path.insert(0, "src")

from loop_observation import (  # noqa: E402
    compose_loop_observation,
    my_corridor_fact,
    sensed_others,
)
from loop_world import RobotState, WorldState  # noqa: E402


def make_state(**robots):
    """robots: name -> (position, direction, corner)."""
    return WorldState({
        name: RobotState(name=name, position=pos, direction=direction, corner=corner)
        for name, (pos, direction, corner) in robots.items()
    })


# --- my_corridor_fact -------------------------------------------------


def test_my_corridor_fact_reports_distance_when_approaching():
    state = make_state(R1=(2, 1, "TL"))  # north's cw boundary is at 5 - 3 cells out
    text = my_corridor_fact(state, "R1")
    assert text == "You are 3 cell(s) from the north corridor entrance."


def test_my_corridor_fact_reports_stopped_at_boundary():
    state = make_state(R1=(5, 1, "TL"))  # exactly at north's cw boundary
    text = my_corridor_fact(state, "R1")
    assert text == "You are stopped at your boundary before the north corridor."


def test_my_corridor_fact_reports_inside_a_corridor():
    state = make_state(R1=(8, 1, "TL"))  # inside north [6,11]
    text = my_corridor_fact(state, "R1")
    assert text == "You are inside the north corridor, currently passing through."


# --- sensed_others: opposing (the real negotiation case) ---------------


def test_opposing_robot_within_range_is_sensed_with_its_corridor_distance():
    state = make_state(
        R1=(5, 1, "TL"),    # north cw boundary
        R2=(12, -1, "TR"),  # north ccw boundary - gap is 7, within sensor_range=10
    )
    facts = sensed_others(state, "R1", sensor_range=10)
    assert len(facts) == 1
    f = facts[0]
    assert f["name"] == "R2"
    assert f["relation"] == "opposing"
    assert f["corridor"] == "north"
    assert f["corridor_distance"] == 0  # R2 is itself at its boundary
    assert f["in_zone"] is False


def test_opposing_robot_outside_sensor_range_is_not_sensed():
    state = make_state(
        R1=(5, 1, "TL"),
        R2=(38, -1, "BL"),  # far away, approaching south, not north
    )
    facts = sensed_others(state, "R1", sensor_range=10)
    assert facts == []


def test_opposing_robot_already_inside_the_corridor():
    state = make_state(
        R1=(5, 1, "TL"),   # at boundary, hasn't entered
        R2=(9, -1, "TR"),  # inside north corridor already
    )
    facts = sensed_others(state, "R1", sensor_range=10)
    assert len(facts) == 1
    f = facts[0]
    assert f["relation"] == "opposing"
    assert f["in_zone"] is True
    assert f["corridor"] == "north"
    assert f["corridor_distance"] is None  # nothing left to report - it's already there


# --- sensed_others: same-lane ahead/behind ------------------------------


def test_same_lane_robot_ahead_is_reported_as_ahead():
    state = make_state(
        R1=(0, 1, "TL"),
        R2=(3, 1, "TL"),  # further along in the same CW direction
    )
    facts = sensed_others(state, "R1", sensor_range=10)
    assert len(facts) == 1
    assert facts[0]["relation"] == "ahead"
    assert facts[0]["corridor"] is None


def test_same_lane_robot_behind_is_reported_as_behind():
    state = make_state(
        R1=(3, 1, "TL"),
        R2=(0, 1, "TL"),  # earlier in the same CW direction
    )
    facts = sensed_others(state, "R1", sensor_range=10)
    assert len(facts) == 1
    assert facts[0]["relation"] == "behind"


def test_opposite_lane_at_the_same_cell_is_not_ahead_or_behind():
    """A robot heading the other way is never "ahead/behind" in my lane -
    it's either genuinely opposing me toward a shared corridor, or (if
    approaching a different corridor than me) still labeled opposing,
    never mistaken for a same-lane relation."""
    state = make_state(
        R1=(5, 1, "TL"),
        R2=(12, -1, "TR"),
    )
    facts = sensed_others(state, "R1", sensor_range=10)
    assert facts[0]["relation"] == "opposing"


# --- compose_loop_observation: the full three-part string --------------


def test_compose_includes_all_three_parts_when_something_is_sensed():
    state = make_state(
        R1=(5, 1, "TL"),
        R2=(12, -1, "TR"),
    )
    text = compose_loop_observation(state, "R1", sensor_range=10, private_text="Battery critical.")
    assert "stopped at your boundary before the north corridor" in text
    assert "R2 is approaching from the other direction" in text
    assert "Battery critical." in text


def test_compose_omits_the_sensed_line_when_nothing_is_in_range():
    state = make_state(
        R1=(5, 1, "TL"),
        R2=(38, -1, "BL"),
    )
    text = compose_loop_observation(state, "R1", sensor_range=10, private_text="No rush.")
    assert "R2" not in text
    assert "No rush." in text


def test_compose_reports_multiple_sensed_others_independently():
    """The roadmap's actual point: 'a list of sensed others', not one
    fixed other_* slot - two robots sensed at once must both appear."""
    state = make_state(
        R1=(5, 1, "TL"),
        R2=(12, -1, "TR"),   # opposing, at north
        R3=(3, 1, "TL"),     # same lane, behind R1
    )
    text = compose_loop_observation(state, "R1", sensor_range=10, private_text="x")
    assert "R2 is approaching from the other direction" in text
    assert "R3 is behind you in your lane" in text
