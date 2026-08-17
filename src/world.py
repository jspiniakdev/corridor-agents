"""Phase 3: the corridor becomes a real 1D grid, and the negotiated decision
causes real movement. See docs/PLAN.md §5 (Phase 3) and §4 (the layer split).

Positions run 1 to 8. CORRIDOR_ZONE is a one-robot-wide hallway: being
anywhere in it at the same time as the other robot is a collision, not just
sharing the exact same cell.

Robot A starts at 1 and moves toward 8. Robot B starts at 8 and moves toward
1. Each has a boundary position - the last safe cell before the zone - where
it must stop and wait if it doesn't yet have permission to enter.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from negotiation import POLICIES, Robot, negotiate
from observation import compose_observation

CORRIDOR_ZONE = {3, 4, 5}

A_START = 1
A_TARGET = 8
A_BOUNDARY = 2
A_DIRECTION = 1

B_START = 8
B_TARGET = 1
B_BOUNDARY = 6
B_DIRECTION = -1

# Not detected at the start (gap 7), but always detected by the time a robot
# reaches its own boundary, even in the worst case where the other robot
# hasn't moved yet (gap 6 after one move). 6 is the only integer for which
# both of those hold.
SENSOR_RANGE = 6


@dataclass
class RobotState:
    """One robot's position and movement info on the grid, plus the
    underlying negotiation.Robot it wraps (for its policy and situation)."""

    robot: Robot
    position: int
    direction: int  # +1 for Robot A, -1 for Robot B
    boundary: int
    target: int

    @property
    def at_boundary(self) -> bool:
        return self.position == self.boundary

    @property
    def in_zone(self) -> bool:
        return self.position in CORRIDOR_ZONE

    @property
    def cleared_zone(self) -> bool:
        """True once this robot has moved all the way past the zone."""
        if self.direction == 1:
            return self.position > max(CORRIDOR_ZONE)
        return self.position < min(CORRIDOR_ZONE)

    @property
    def reached_target(self) -> bool:
        return self.position == self.target


@dataclass
class WorldState:
    a: RobotState
    b: RobotState
    scenario: object = None
    tick: int = 0
    priority: str | None = None  # once resolved, the name of the robot that goes first
    negotiation_outcome: object = None  # the negotiation.Outcome, once one has happened
    negotiation_tick: int | None = None  # the tick negotiate() was actually called on, if any
    log: list = field(default_factory=list)


def _next_position(robot_state: RobotState, action: str) -> int:
    return robot_state.position + robot_state.direction if action == "move" else robot_state.position


def reactive_filter(state: WorldState, a_action: str, b_action: str) -> tuple[str, str]:
    """The safety net. Recomputes whether these actions are safe,
    independently of whatever decided them - so a bug upstream can never
    cause a real collision, at worst it causes one robot to wait when it
    didn't strictly need to.

    Two separate checks, both needed:

    1. Entry gate - a robot may only START entering the corridor zone (the
       one-lane bridge) if the other robot isn't currently anywhere in it,
       even if the other is leaving on this same tick. Without this, one
       robot could enter from one end while the other exits from the other
       end in the same tick - passing through each other on the bridge.
    2. Backstop - never let both robots' next positions land in the zone at
       once, however that was decided. Covers e.g. both trying to enter
       simultaneously while the zone is genuinely empty.
    """
    a_next = _next_position(state.a, a_action)
    b_next = _next_position(state.b, b_action)

    a_entering = a_next in CORRIDOR_ZONE and state.a.position not in CORRIDOR_ZONE
    b_entering = b_next in CORRIDOR_ZONE and state.b.position not in CORRIDOR_ZONE

    if a_entering and state.b.position in CORRIDOR_ZONE:
        a_action = "wait"
    if b_entering and state.a.position in CORRIDOR_ZONE:
        b_action = "wait"

    a_next = _next_position(state.a, a_action)
    b_next = _next_position(state.b, b_action)

    if a_next in CORRIDOR_ZONE and b_next in CORRIDOR_ZONE:
        if state.a.in_zone and not state.b.in_zone:
            # A is already passing through - B must not enter too.
            b_action = "wait"
        elif state.b.in_zone and not state.a.in_zone:
            a_action = "wait"
        else:
            # Neither is in the zone yet and both are trying to enter at
            # once - should be unreachable if the executive layer is
            # correct, but block both rather than trust that.
            a_action = "wait"
            b_action = "wait"

    return a_action, b_action


def apply(state: WorldState, a_action: str, b_action: str) -> None:
    """Move each robot one cell, or leave it in place, per its action."""
    if a_action == "move":
        state.a.position += state.a.direction
    if b_action == "move":
        state.b.position += state.b.direction


def build_world(scenario, policy_a_name: str, policy_b_name: str) -> WorldState:
    """Build a fresh WorldState the same way run.py/eval.py build a Robot for
    Phase 1/2 - the scenario's static situation text is what LLMPolicy will
    see, unless something later replaces it with a computed observation."""
    policy_a = POLICIES[policy_a_name]()
    policy_b = POLICIES[policy_b_name]()
    robot_a = Robot("Robot A", scenario.a_situation, scenario.a_urgency, policy_a)
    robot_b = Robot("Robot B", scenario.b_situation, scenario.b_urgency, policy_b)
    a = RobotState(robot_a, A_START, A_DIRECTION, A_BOUNDARY, A_TARGET)
    b = RobotState(robot_b, B_START, B_DIRECTION, B_BOUNDARY, B_TARGET)
    return WorldState(a, b, scenario)


def tie_break(state: WorldState) -> str:
    """FCFS's only real rule: a genuine simultaneous-arrival tie goes to
    Robot A. Deterministic, no randomness, so baseline results stay
    reproducible without needing to seed anything."""
    return state.a.robot.name


def _negotiate_priority(state: WorldState, max_negotiation_turns: int) -> str | None:
    """Ask the two robots to negotiate. First, each robot's situation is
    replaced with a freshly computed observation - position and sensor
    facts, plus the same private text scenarios.py always provided - since
    this is the one moment that observation actually matters. Robot A always
    speaks first (negotiate()'s own rule), a deliberate, fixed convention so
    tests stay deterministic - not an accidental asymmetry."""
    state.a.robot.situation = compose_observation(
        state.a.position, state.a.direction, state.b.position, CORRIDOR_ZONE, SENSOR_RANGE, state.scenario.a_situation
    )
    state.b.robot.situation = compose_observation(
        state.b.position, state.b.direction, state.a.position, CORRIDOR_ZONE, SENSOR_RANGE, state.scenario.b_situation
    )
    state.negotiation_tick = state.tick
    outcome = negotiate(state.a.robot, state.b.robot, max_turns=max_negotiation_turns)
    state.negotiation_outcome = outcome
    return outcome.agreed_on


def _within_sensor_range(a: RobotState, b: RobotState) -> bool:
    return abs(a.position - b.position) <= SENSOR_RANGE


def _resolve_priority(state: WorldState, deliberate: bool, max_negotiation_turns: int) -> None:
    """Figure out who goes first, if this tick is the moment that can be
    decided. Sets state.priority at most once - it is never recomputed once
    set, even if it's None (a deadlock), so a stuck negotiation never gets
    re-run tick after tick.

    In deliberate mode, a robot at its boundary negotiates as soon as it can
    SENSE the other robot, even if the other hasn't reached its own boundary
    yet - it doesn't need to wait for an exact simultaneous arrival. FCFS
    (deliberate=False) keeps the strict old rule instead: only an exact
    double-arrival is a real tie. Broadening FCFS the same way would corrupt
    what FCFS means - it's specifically "no communication, strict arrival
    order," and a robot that genuinely arrived first should stay the winner,
    not get overridden by a tie-break it didn't actually tie for."""
    a_arrived = state.a.at_boundary
    b_arrived = state.b.at_boundary

    if deliberate:
        a_sensed_conflict = a_arrived and _within_sensor_range(state.a, state.b)
        b_sensed_conflict = b_arrived and _within_sensor_range(state.b, state.a)
        if a_sensed_conflict or b_sensed_conflict:
            state.priority = _negotiate_priority(state, max_negotiation_turns)
            return

    if a_arrived and b_arrived:
        # FCFS's only real tie: both robots arrived on the exact same tick.
        state.priority = tie_break(state)
    elif a_arrived:
        # A arrived first - no real conflict exists yet, so it just claims
        # priority and proceeds. This is also exactly what FCFS is.
        state.priority = state.a.robot.name
    elif b_arrived:
        state.priority = state.b.robot.name
    # else: neither robot has reached its boundary yet - nothing to resolve.


def _decide_one(me: RobotState, other: RobotState, priority: str | None) -> str:
    """One robot's move/wait decision, given who (if anyone) currently has
    priority."""
    if me.reached_target:
        return "wait"  # nowhere left to go

    if not me.at_boundary:
        return "move"  # outside the zone/boundary, no conflict is possible

    if priority == me.robot.name:
        return "move"  # it's my turn

    if priority == other.robot.name:
        return "move" if other.cleared_zone else "wait"  # wait for my turn

    # priority is still unresolved and I'm sitting at the boundary - this
    # shouldn't normally happen, since _resolve_priority always sets
    # priority the moment both robots have arrived. Wait rather than guess.
    return "wait"


def executive_decide(state: WorldState, deliberate: bool, max_negotiation_turns: int) -> tuple[str, str]:
    """The executive layer: decide each robot's move/wait action for this
    tick. Resolves priority first if it isn't already known."""
    if state.priority is None:
        _resolve_priority(state, deliberate, max_negotiation_turns)

    a_action = _decide_one(state.a, state.b, state.priority)
    b_action = _decide_one(state.b, state.a, state.priority)
    return a_action, b_action


def step(state: WorldState, deliberate: bool, max_negotiation_turns: int) -> WorldState:
    """Advance the world by one tick: executive decides, reactive layer
    double-checks, then the decided actions are applied."""
    a_action, b_action = executive_decide(state, deliberate, max_negotiation_turns)
    a_action, b_action = reactive_filter(state, a_action, b_action)
    apply(state, a_action, b_action)
    state.log.append((state.tick, state.a.position, state.b.position, a_action, b_action, state.priority))
    state.tick += 1
    return state


@dataclass
class EpisodeResult:
    log: list
    ticks_used: int
    completed: bool
    priority: str | None = None  # who was given priority, however it was decided
    negotiation: object = None  # the negotiation.Outcome, if a standoff happened
    negotiation_tick: int | None = None  # which tick the standoff happened on, if any


def run_episode(
    scenario,
    policy_a_name: str,
    policy_b_name: str,
    deliberate: bool = True,
    max_ticks: int = 30,
    max_negotiation_turns: int = 6,
) -> EpisodeResult:
    """Run one Phase 3 episode: build the world, then tick until both robots
    reach their targets, a negotiation deadlocks, or max_ticks runs out."""
    state = build_world(scenario, policy_a_name, policy_b_name)

    while state.tick < max_ticks:
        if state.a.reached_target and state.b.reached_target:
            return EpisodeResult(state.log, state.tick, True, state.priority, state.negotiation_outcome, state.negotiation_tick)

        if state.negotiation_outcome is not None and not state.negotiation_outcome.agreed:
            break  # terminal deadlock - no point burning the remaining ticks

        step(state, deliberate, max_negotiation_turns)

    return EpisodeResult(state.log, state.tick, False, state.priority, state.negotiation_outcome, state.negotiation_tick)
