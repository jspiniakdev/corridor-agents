"""Tests for the Phase 3 observation composition (PLAN.md §4.1's "Job 4").
Zero API calls - observation.py has no dependency on the network, the world
loop, or anything beyond plain values.
"""

import sys

sys.path.insert(0, "src")

from negotiation import Robot  # noqa: E402
from observation import compose_observation, compose_observation_from_dict, distance_to_entrance  # noqa: E402
from scenarios import SCENARIOS  # noqa: E402
from world import (  # noqa: E402
    A_BOUNDARY,
    A_DIRECTION,
    B_BOUNDARY,
    B_DIRECTION,
    CORRIDOR_ZONE,
    RobotState,
    SENSOR_RANGE,
    WorldState,
    executive_decide,
)

S = SCENARIOS[0]


def test_distance_to_entrance_for_robot_a():
    assert distance_to_entrance(position=1, direction=1, corridor_zone=CORRIDOR_ZONE) == 2
    assert distance_to_entrance(position=2, direction=1, corridor_zone=CORRIDOR_ZONE) == 1


def test_distance_to_entrance_for_robot_b():
    assert distance_to_entrance(position=8, direction=-1, corridor_zone=CORRIDOR_ZONE) == 3
    assert distance_to_entrance(position=6, direction=-1, corridor_zone=CORRIDOR_ZONE) == 1


def test_sensor_fact_present_within_range():
    text = compose_observation(2, 1, 2 + SENSOR_RANGE, CORRIDOR_ZONE, SENSOR_RANGE, "private")
    assert "Another robot" in text


def test_sensor_fact_includes_the_other_robots_distance_to_its_entrance():
    # the exact standoff from the reviewed episode: A at 2, B at 6 - both
    # one cell from the corridor. The LLM had claimed B was 4 cells away.
    text = compose_observation(2, 1, 6, CORRIDOR_ZONE, SENSOR_RANGE, "private")
    assert "You are 1 cell(s) from the corridor entrance." in text
    assert "It is 1 cell(s) from the corridor entrance on its side." in text


def test_sensor_fact_absent_just_outside_range():
    text = compose_observation(2, 1, 2 + SENSOR_RANGE + 1, CORRIDOR_ZONE, SENSOR_RANGE, "private")
    assert "Another robot" not in text


def test_private_text_appears_verbatim():
    text = compose_observation(2, 1, 6, CORRIDOR_ZONE, SENSOR_RANGE, "the exact private sentence")
    assert "the exact private sentence" in text


def test_urgency_never_appears_in_a_composed_observation():
    """Same invariant test_negotiation.py already enforces for the static
    scenario text, extended to the new computed observation. Checking for
    the word "urgency" only, not the numeric value - a small integer like
    "1" would coincidentally match unrelated digits elsewhere in the text
    (e.g. "1 cell(s) from the entrance"), which isn't a real leak."""
    for scenario in SCENARIOS:
        for position in range(1, 9):
            text = compose_observation(position, 1, 5, CORRIDOR_ZONE, SENSOR_RANGE, scenario.a_situation)
            assert "urgency" not in text.lower()

    # compose_observation has no way to leak urgency even in principle: its
    # signature doesn't accept it as an input at all.
    import inspect

    assert "urgency" not in inspect.signature(compose_observation).parameters


class RecordingPolicy:
    """Records the exact situation string it was handed, so a test can
    confirm negotiation actually receives the fresh computed observation -
    not the original static scenario text."""

    def __init__(self):
        self.seen_situation = None

    def respond(self, me, other, history, max_turns):
        self.seen_situation = me.situation
        from negotiation import Intent, Message

        return Message(me.name, Intent.ACCEPT, other.name)


def test_negotiation_actually_receives_the_computed_observation_not_raw_scenario_text():
    a_policy = RecordingPolicy()
    b_policy = RecordingPolicy()
    robot_a = Robot("Robot A", S.a_situation, S.a_urgency, a_policy)
    robot_b = Robot("Robot B", S.b_situation, S.b_urgency, b_policy)
    a = RobotState(robot_a, A_BOUNDARY, A_DIRECTION, A_BOUNDARY, 8)
    b = RobotState(robot_b, B_BOUNDARY, B_DIRECTION, B_BOUNDARY, 1)
    state = WorldState(a, b, S)

    executive_decide(state, deliberate=True, max_negotiation_turns=6)

    assert "cell(s) from the corridor entrance" in a_policy.seen_situation
    assert a_policy.seen_situation != S.a_situation


# --- compose_observation_from_dict (D24, the networked equivalent) ---------


def test_from_dict_includes_sensor_fact_when_sensed():
    obs = {
        "distance_to_entrance": 1,
        "sensed_other": True,
        "gap_if_sensed": 6,
        "other_distance_to_entrance": 3,
        "other_distance_to_boundary": 4,
    }
    text = compose_observation_from_dict(obs, "Robot B", "private")
    assert "Robot B" in text
    assert "6 cell(s) away" in text
    assert "3 cell(s) from the corridor entrance on its side" in text
    assert "4 cell(s) from its own boundary" in text
    assert text.endswith("private")


def test_from_dict_omits_sensor_facts_when_not_sensed():
    obs = {
        "distance_to_entrance": 1,
        "sensed_other": False,
        "gap_if_sensed": None,
        "other_distance_to_entrance": None,
        "other_distance_to_boundary": None,
    }
    text = compose_observation_from_dict(obs, "Robot B", "private")
    assert "Robot B" not in text
    assert "own boundary" not in text
    assert "on its side" not in text


def test_from_dict_omits_other_boundary_fact_when_sensed_but_unknown():
    # sensed_other True with other_distance_to_boundary missing shouldn't
    # happen in practice (world_server.py always fills it in when sensed),
    # but the function should degrade gracefully rather than crash.
    obs = {"distance_to_entrance": 1, "sensed_other": True, "gap_if_sensed": 6}
    text = compose_observation_from_dict(obs, "Robot B", "private")
    assert "6 cell(s) away" in text
    assert "own boundary" not in text


def test_from_dict_never_leaks_urgency():
    import inspect

    assert "urgency" not in inspect.signature(compose_observation_from_dict).parameters
