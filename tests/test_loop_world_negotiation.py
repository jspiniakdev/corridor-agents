"""Tests for Phase 11a step 2: docs/PHASE_11_ROADMAP.md's negotiation
trigger, per-corridor contest lifecycle, and life/urgency/death mechanic
(the additions to src/loop_world.py beyond the pure reactive layer covered
in test_loop_world.py). Uses negotiation.py's scripted policies throughout
(never_yield / always_yield) - deterministic and free, zero LLM calls,
matching world.py's own test_world.py convention. See docs/DECISIONS.md D48.
"""

import sys

sys.path.insert(0, "src")

from loop_world import (  # noqa: E402
    RobotState,
    WorldState,
    apply_drain_and_death,
    contender_for,
    decide_one,
    reactive_filter,
    resolve_contests,
    step,
    survival_result,
)
from negotiation import POLICIES, Robot as NegoRobot  # noqa: E402


class ExplodingPolicy:
    """Fails the test loudly if negotiate() is ever actually invoked with
    it - used to prove a lone arrival claims for free, no negotiation."""

    def respond(self, me, other, history, max_turns):
        raise AssertionError(f"{me.name}: negotiate() should not have been called")


def make_robot(name, position, direction, corner, policy_name="always_yield", urgency=10.0, life=100.0):
    policy = POLICIES[policy_name]() if isinstance(policy_name, str) else policy_name
    nego = NegoRobot(name=name, situation="", urgency=int(urgency), policy=policy)
    return RobotState(name=name, position=position, direction=direction, corner=corner,
                       robot=nego, life=life, urgency=urgency)


def make_state(*robots: RobotState) -> WorldState:
    return WorldState({r.name: r for r in robots})


# --- lone arrival: free, zero negotiation ---------------------------------


def test_lone_arrival_claims_the_corridor_for_free():
    state = make_state(
        make_robot("R1", 5, 1, "TL", policy_name=ExplodingPolicy()),  # at North's CW boundary, alone
    )
    resolve_contests(state)
    contest = state.contests["north"]
    assert contest is not None
    assert contest.goes_first == "R1"
    assert contest.participants == frozenset({"R1"})
    assert decide_one(state, "R1") == "move"


def test_contender_for_is_none_when_nobody_is_within_sensor_range():
    state = make_state(
        make_robot("R1", 5, 1, "TL"),
        make_robot("R2", 38, -1, "BL"),  # far away, approaching South, not North
    )
    assert contender_for(state, "R1", "north") is None


# --- a real two-robot negotiation ------------------------------------------


def test_two_robots_negotiate_and_whoever_goes_first_proceeds_first():
    # NeverYield vs AlwaysYield: deterministic, single-round resolution in
    # favor of the NeverYield side, no deadlock.
    state = make_state(
        make_robot("R1", 5, 1, "TL", policy_name="never_yield"),
        make_robot("R2", 12, -1, "TR", policy_name="always_yield"),
    )
    resolve_contests(state)
    contest = state.contests["north"]
    assert contest is not None
    assert contest.deadlocked is False
    assert contest.goes_first == "R1"
    assert contest.participants == frozenset({"R1", "R2"})
    assert decide_one(state, "R1") == "move"
    assert decide_one(state, "R2") == "wait"  # R1 hasn't cleared yet


def test_the_one_waiting_drains_then_proceeds_once_the_other_clears():
    state = make_state(
        make_robot("R1", 5, 1, "TL", policy_name="never_yield", urgency=7.0),
        make_robot("R2", 12, -1, "TR", policy_name="always_yield", urgency=3.0),
    )
    r2_start_life = state.robots["R2"].life

    result = step(state)
    assert result["resolved"]["R1"] == "move"
    assert result["resolved"]["R2"] == "wait"
    assert state.robots["R2"].life == r2_start_life - 3.0  # drained by its own urgency
    assert state.robots["R1"].life == 100.0  # the one moving never drains

    # Run until R1 (length-6 North corridor) fully clears.
    for _ in range(6):
        step(state)

    assert state.robots["R1"].in_zone is False
    # R2 should now be allowed through - re-decide with the same contest.
    assert decide_one(state, "R2") == "move"


def test_going_first_is_not_the_same_as_surviving_longer():
    """The whole point of dropping "winner": R1 goes first here (it's the
    urgent one), but R2 - which waited and drained - can still end up
    having survived just as long, or longer, depending on how the run
    continues. survival_result reports ticks alive, nothing about who
    went first at any corridor."""
    state = make_state(
        make_robot("R1", 5, 1, "TL", policy_name="never_yield", urgency=7.0),
        make_robot("R2", 12, -1, "TR", policy_name="always_yield", urgency=3.0),
    )
    for _ in range(3):
        step(state)
    result = survival_result(state)
    assert result == {"R1": state.tick, "R2": state.tick}  # both still alive, same duration so far


def test_deadlock_drains_both_sides_until_death():
    state = make_state(
        make_robot("R1", 5, 1, "TL", policy_name="never_yield", urgency=20.0),
        make_robot("R2", 12, -1, "TR", policy_name="never_yield", urgency=20.0),
    )
    result = step(state)  # tick 1
    assert result["resolved"]["R1"] == "wait"
    assert result["resolved"]["R2"] == "wait"
    assert state.contests["north"].deadlocked is True
    assert state.robots["R1"].life == 80.0
    assert state.robots["R2"].life == 80.0
    assert result["dead"] == []

    for _ in range(3):
        result = step(state)  # ticks 2, 3, 4
    assert state.robots["R1"].life == 20.0
    assert state.robots["R2"].life == 20.0
    assert result["dead"] == []

    result = step(state)  # tick 5: life hits exactly 0 and is removed the same tick
    assert set(result["dead"]) == {"R1", "R2"}
    assert state.robots == {}
    assert survival_result(state) == {"R1": 5, "R2": 5}


def test_survival_result_distinguishes_the_dead_from_the_living():
    # R1 goes first (never_yield beats always_yield) and never drains; R2
    # loses, and at urgency=100 dies on the very first drained tick - the
    # point being that going first (R1) and surviving (also R1, here) are
    # two different things this module never conflates, even though they
    # happen to coincide in this particular matchup.
    state = make_state(
        make_robot("R1", 5, 1, "TL", policy_name="never_yield", urgency=1.0),
        make_robot("R2", 12, -1, "TR", policy_name="always_yield", urgency=100.0),
    )
    step(state)
    result = survival_result(state)
    assert result["R2"] == 1  # died tick 1
    assert result["R1"] == 1  # still alive, survived (at least) 1 tick so far


def test_negotiation_is_free_no_drain_before_a_contest_resolves():
    """A robot that hasn't yet had its contest resolved this tick (the
    defensive fallback in decide_one) must never drain - only a *resolved*
    contest costs life. Exercised directly: apply_drain_and_death must be a
    no-op for a "wait" with no contest recorded at all."""
    state = make_state(make_robot("R1", 5, 1, "TL", urgency=50.0))
    dead = apply_drain_and_death(state, {"R1": "wait"})
    assert dead == []
    assert state.robots["R1"].life == 100.0


# --- contest retirement ----------------------------------------------------


def test_contest_retires_once_both_participants_have_moved_past_it():
    state = make_state(
        make_robot("R1", 5, 1, "TL", policy_name="never_yield"),
        make_robot("R2", 12, -1, "TR", policy_name="always_yield"),
    )
    resolve_contests(state)
    assert state.contests["north"] is not None

    # Move both robots well past North (position 20, 4) without going
    # through resolve_contests/step again - direct manipulation, to
    # isolate the retirement check itself.
    state.robots["R1"].position = 20
    state.robots["R2"].position = 4
    resolve_contests(state)
    assert state.contests["north"] is None


def test_a_retired_corridor_negotiates_fresh_for_the_next_encounter():
    state = make_state(
        make_robot("R1", 5, 1, "TL", policy_name="never_yield"),
        make_robot("R2", 12, -1, "TR", policy_name="always_yield"),
    )
    resolve_contests(state)
    first_contest = state.contests["north"]

    state.robots["R1"].position = 20
    state.robots["R2"].position = 4
    resolve_contests(state)
    assert state.contests["north"] is None

    # Put both back at their boundaries as if they've lapped around again.
    state.robots["R1"].position = 5
    state.robots["R2"].position = 12
    resolve_contests(state)
    second_contest = state.contests["north"]
    assert second_contest is not None
    assert second_contest is not first_contest


# --- collision safety still holds with the executive layer wired in -------


def test_full_step_never_lets_reactive_filter_be_bypassed():
    """Even with real (scripted) negotiation deciding priority, the
    reactive layer is still the actual authority on who moves - step()
    must route every decision through it, not trust decide_one blindly."""
    state = make_state(
        make_robot("R1", 5, 1, "TL", policy_name="never_yield"),
        make_robot("R2", 12, -1, "TR", policy_name="never_yield"),  # deadlock: both propose "wait" forever
    )
    for _ in range(20):
        result = step(state)
        occupants = [n for n in state.robots if state.robots[n].in_zone]
        assert len(occupants) <= 1
        if not state.robots:
            break
