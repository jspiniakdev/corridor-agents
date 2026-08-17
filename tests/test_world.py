"""Tests for the Phase 3 world/tick loop. Only deterministic policies (or a
call-counting stub) are ever used here - zero API calls.
"""

import sys

sys.path.insert(0, "src")

from negotiation import NeverYield, Robot, Stubborn  # noqa: E402
from scenarios import SCENARIOS  # noqa: E402
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
    executive_decide,
    run_episode,
)

S = SCENARIOS[0]


class CountingPolicy:
    """Records how many times respond() was called. Used to prove a code
    path never actually asks a policy for a decision."""

    def __init__(self):
        self.calls = 0

    def respond(self, me, other, history, max_turns):
        self.calls += 1
        return None


def make_state(a_position, b_position, a_policy=None, b_policy=None):
    robot_a = Robot("Robot A", S.a_situation, S.a_urgency, a_policy or Stubborn())
    robot_b = Robot("Robot B", S.b_situation, S.b_urgency, b_policy or Stubborn())
    a = RobotState(robot_a, a_position, A_DIRECTION, A_BOUNDARY, A_TARGET)
    b = RobotState(robot_b, b_position, B_DIRECTION, B_BOUNDARY, B_TARGET)
    return WorldState(a, b, S)


def test_first_arriver_proceeds_without_any_tie_break():
    state = make_state(a_position=A_BOUNDARY, b_position=B_START)  # only A has arrived
    a_action, _ = executive_decide(state, deliberate=False, max_negotiation_turns=6)
    assert state.priority == "Robot A"
    assert a_action == "move"


def test_simultaneous_arrival_in_fcfs_mode_resolves_via_tie_break_only():
    a_policy = CountingPolicy()
    b_policy = CountingPolicy()
    state = make_state(A_BOUNDARY, B_BOUNDARY, a_policy=a_policy, b_policy=b_policy)
    executive_decide(state, deliberate=False, max_negotiation_turns=6)
    assert state.priority == "Robot A"  # tie_break's fixed rule
    assert a_policy.calls == 0
    assert b_policy.calls == 0


def test_fcfs_episode_completes():
    result = run_episode(S, "stubborn", "never_yield", deliberate=False)
    assert result.completed is True


def test_zone_occupancy_invariant_holds_across_an_fcfs_episode():
    result = run_episode(S, "stubborn", "never_yield", deliberate=False)
    for _tick, a_position, b_position, _a_action, _b_action in result.log:
        assert not (a_position in CORRIDOR_ZONE and b_position in CORRIDOR_ZONE)


def test_simultaneous_standoff_with_deliberation_produces_a_winner():
    state = make_state(A_BOUNDARY, B_BOUNDARY, a_policy=Stubborn(), b_policy=Stubborn())
    executive_decide(state, deliberate=True, max_negotiation_turns=6)
    assert state.priority in ("Robot A", "Robot B")
    assert state.negotiation_outcome is not None
    assert state.negotiation_outcome.agreed


def test_never_yield_standoff_deadlocks_and_negotiate_is_called_exactly_once():
    """Critical regression test: once a standoff deadlocks, running the world
    for more ticks must NOT call negotiate()/.respond() again. A model call
    every tick is exactly the failure mode PLAN.md warns against."""
    a_policy = NeverYield()
    b_policy = NeverYield()
    state = make_state(A_BOUNDARY, B_BOUNDARY, a_policy=a_policy, b_policy=b_policy)

    for _ in range(10):
        executive_decide(state, deliberate=True, max_negotiation_turns=6)

    assert state.negotiation_outcome is not None
    assert not state.negotiation_outcome.agreed
    # Two NeverYield robots exchange 6 messages before giving up (max_turns).
    # If negotiate() had been called more than once, this would be a multiple of 6.
    assert state.negotiation_outcome.messages_used == 6


def test_no_standoff_means_no_negotiation_at_all():
    """When the other robot is genuinely out of sensor range, the
    deliberative layer must stay completely silent. b_position=100 is
    outside the real 1-8 grid on purpose - within the real grid, once a
    robot reaches its boundary the other is always within SENSOR_RANGE (the
    max possible gap from a boundary position is exactly SENSOR_RANGE), so
    this exercises the "can't sense" branch directly rather than relying on
    a scenario that can't actually occur in a real episode."""
    state = make_state(a_position=A_BOUNDARY, b_position=100)
    executive_decide(state, deliberate=True, max_negotiation_turns=6)
    assert state.negotiation_outcome is None


def test_at_boundary_and_other_not_yet_arrived_but_sensed_still_negotiates():
    """The actual new behavior: A doesn't need to wait for B to physically
    reach its own boundary - sensing it is enough to start negotiating."""
    state = make_state(a_position=A_BOUNDARY, b_position=B_START)  # gap is exactly SENSOR_RANGE
    executive_decide(state, deliberate=True, max_negotiation_turns=6)
    assert state.negotiation_outcome is not None


def test_fcfs_mode_is_unaffected_by_sensing_early():
    """FCFS must keep its strict rule: a robot that genuinely arrived first
    stays the winner, even though it can already sense the other robot -
    broadening this the way deliberate mode is would corrupt what FCFS
    means (no communication, strict arrival order)."""
    a_policy = CountingPolicy()
    b_policy = CountingPolicy()
    state = make_state(A_BOUNDARY, B_START, a_policy=a_policy, b_policy=b_policy)
    executive_decide(state, deliberate=False, max_negotiation_turns=6)
    assert state.priority == "Robot A"
    assert state.negotiation_outcome is None
    assert a_policy.calls == 0
    assert b_policy.calls == 0
