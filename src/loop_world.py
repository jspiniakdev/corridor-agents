"""Phase 11a, step 1: the loop world ("the O") - geometry and the reactive
safety layer only. See docs/PHASE_11_ROADMAP.md for the full design and
docs/DECISIONS.md D48 for why Phase 9 became this instead of the
originally-scoped N-way-negotiation bundle.

Lives alongside world.py, not in place of it (D48's file-layout note):
world_server.py/world_store.py/agent.py and the deployed three-Cloud-Run-
Service pipeline all import the linear-grid world.py directly, and none of
that gets touched until Phase 11c. Nothing here is imported by anything
else yet.

This module is the physical substrate only - loop geometry, directional
lanes, two 1-lane pinch-point corridors, and the reactive layer that makes
collision structurally impossible regardless of what any executive or
negotiation layer decides, independently re-derived every tick (the same
discipline as world.py's D12, generalized from two named robots to an
arbitrary named set). Life/urgency/death bookkeeping and the negotiation
trigger are a follow-up addition (11a step 2) - this part is pure
position/collision arithmetic. Zero LLM, zero negotiation, involved here.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from negotiation import negotiate

PERIMETER = 44

CORNERS = {"TL": 0, "TR": 16, "BR": 22, "BL": 38}

# Each corridor: inclusive (start, end) span. Revised from an original
# north=[8,13]/south=[25,28] draft during design review (D47/D48's file
# note) - that placement let one direction's robot reach, cross, and clear
# a corridor before the other robot ever came within SENSOR_RANGE,
# skipping negotiation entirely on the project's own flagship `duel`
# scenario. Confirmed by simulation, not just re-derived arithmetic - see
# docs/PHASE_11_ROADMAP.md.
CORRIDORS = {
    "north": {"start": 6, "end": 11},
    "south": {"start": 27, "end": 30},
}

SENSOR_RANGE = 10  # whole corridor (len<=6) + ~4 cells past the far mouth


def cw_boundary(corridor: str) -> int:
    """The stop-and-wait cell for a CW (increasing-position) robot: one
    cell before the corridor's span, in the direction of travel."""
    return CORRIDORS[corridor]["start"] - 1


def ccw_boundary(corridor: str) -> int:
    """The stop-and-wait cell for a CCW (decreasing-position) robot: one
    cell past the corridor's span, in the direction of travel."""
    return CORRIDORS[corridor]["end"] + 1


def boundary_for(corridor: str, direction: int) -> int:
    return cw_boundary(corridor) if direction == 1 else ccw_boundary(corridor)


def in_span(position: int, corridor: str) -> bool:
    c = CORRIDORS[corridor]
    return c["start"] <= position <= c["end"]


def corridor_containing(position: int) -> str | None:
    """Which corridor (if any) currently contains `position`. A position
    is in at most one corridor - the spans don't overlap."""
    for name in CORRIDORS:
        if in_span(position, name):
            return name
    return None


def forward_distance(position: int, target: int, direction: int) -> int:
    """Cells to travel from `position` to reach `target`, moving only in
    `direction` (+1 or -1) and wrapping around the loop. 0 if already
    there."""
    if direction == 1:
        return (target - position) % PERIMETER
    return (position - target) % PERIMETER


def next_corridor(position: int, direction: int) -> tuple[str, int]:
    """The next corridor this position/direction will reach (by boundary),
    and the distance in cells to get there. Callers should check `in_span`
    first - a robot already inside a corridor isn't "approaching" one."""
    candidates = [
        (name, forward_distance(position, boundary_for(name, direction), direction))
        for name in CORRIDORS
    ]
    return min(candidates, key=lambda pair: pair[1])


def arc_gap(a: int, b: int) -> int:
    """Shortest distance between two positions around the loop, direction-
    agnostic - the sensing mechanism (D12/D25's "sensed if within range",
    generalized to a loop)."""
    d = abs(a - b) % PERIMETER
    return min(d, PERIMETER - d)


@dataclass
class RobotState:
    """One robot's physical state on the loop. `robot` wraps the
    negotiation.Robot (policy + situation) for the executive/negotiation
    layer to use, same pattern as world.py's RobotState - left unset here
    since this module's own tests are policy-free; populated once 11a's
    negotiation-trigger addition needs it. `life`/`urgency` are carried but
    not yet drained by anything in this module - that bookkeeping is the
    11a step-2 addition, alongside sensing and the negotiation trigger."""

    name: str
    position: int
    direction: int  # +1 = CW (increasing), -1 = CCW (decreasing)
    corner: str
    robot: object = None  # negotiation.Robot, set by the executive layer
    life: float = 100.0
    urgency: float = 10.0

    @property
    def lane(self) -> int:
        """Lane is exactly the direction - CW and CCW never share a lane
        on a 2-lane side (PHASE_11_ROADMAP.md's "Two lanes ... directional
        ... Opposing traffic on a 2-lane side therefore never conflicts")."""
        return self.direction

    @property
    def in_zone(self) -> bool:
        return corridor_containing(self.position) is not None

    @property
    def current_corridor(self) -> str | None:
        return corridor_containing(self.position)

    @property
    def at_boundary(self) -> bool:
        if self.in_zone:
            return False
        _, distance = next_corridor(self.position, self.direction)
        return distance == 0

    @property
    def approaching_corridor(self) -> str:
        """The corridor this robot is either inside, or next approaching."""
        if self.in_zone:
            return self.current_corridor
        name, _ = next_corridor(self.position, self.direction)
        return name


@dataclass
class WorldState:
    robots: dict[str, RobotState] = field(default_factory=dict)
    contests: dict[str, "CorridorContest | None"] = field(
        default_factory=lambda: {name: None for name in CORRIDORS}
    )
    tick: int = 0
    died_at: dict[str, int] = field(default_factory=dict)  # name -> tick it died


def reactive_filter(state: WorldState, proposed: dict[str, str]) -> dict[str, str]:
    """Independently re-derives which proposed "move"s are actually safe -
    generalizes world.py's reactive_filter from exactly two named robots
    (a/b) to an arbitrary named set. Two invariants, matching
    PHASE_11_ROADMAP.md's "The reactive invariant" section:

      1. At most one robot per (lane, position) cell, outside corridors.
      2. At most one robot inside each 1-lane corridor at a time - a
         robot may only *enter* an empty corridor, and simultaneous
         double-entry from both ends is blocked (both wait) rather than
         arbitrated - the same "simultaneous entry attempt blocks both"
         and "swap through the bridge is blocked" guarantees world.py's
         reactive_filter makes, generalized.

    Re-derived from actual positions every tick, regardless of what
    proposed the moves - same discipline as D12, so a bug in the
    executive/negotiation layer can't cause a real collision. `proposed`
    is `{robot_name: "move" | "wait"}` for every robot in `state.robots`.
    """
    resolved = dict(proposed)
    names = list(state.robots)

    def next_position(name: str) -> int:
        robot = state.robots[name]
        if resolved[name] != "move":
            return robot.position
        return (robot.position + robot.direction) % PERIMETER

    def proposed_next_position(name: str) -> int:
        robot = state.robots[name]
        if proposed[name] != "move":
            return robot.position
        return (robot.position + robot.direction) % PERIMETER

    occupied_corridors_now = {
        corridor: [n for n in names if in_span(state.robots[n].position, corridor)]
        for corridor in CORRIDORS
    }

    # Pass 1: corridor entry safety. A robot already inside a corridor,
    # continuing through, is always safe - it already committed, and is
    # never re-checked (a robot in motion never drains, whatever it's
    # called - see the "no winner" note on CorridorContest below).
    # A robot ENTERING a corridor this tick needs it currently empty, and
    # not simultaneously entered by anyone else - computed entirely from
    # the ORIGINAL `proposed` snapshot, not the mutating `resolved` dict,
    # so the outcome doesn't depend on iteration order: two simultaneous
    # entrants must BOTH wait, not "whichever is processed first blocks
    # the other out of the pending-entrants list." (Caught live: an
    # earlier version downgraded the first-processed robot, which then
    # made it invisible to the second robot's own simultaneity check,
    # so the second sailed through unblocked - exactly the asymmetric
    # tiebreak this invariant must not have.)
    entrants_by_corridor: dict[str, list[str]] = {c: [] for c in CORRIDORS}
    for name in names:
        if proposed[name] != "move":
            continue
        robot = state.robots[name]
        np = proposed_next_position(name)
        corridor = corridor_containing(np)
        if corridor is None or in_span(robot.position, corridor):
            continue  # not an entry move
        entrants_by_corridor[corridor].append(name)

    for corridor, entrants in entrants_by_corridor.items():
        if occupied_corridors_now[corridor] or len(entrants) > 1:
            for name in entrants:
                resolved[name] = "wait"

    # Pass 2: same-lane cell collisions outside corridors - "queueing" /
    # following. Only bites with 3+ robots sharing a lane (11b's `crowd`
    # config); with 2 robots (11a - always opposite directions, so always
    # different lanes) this pass never has anything to do. Iterate to a
    # fixed point: a downgrade can cascade (R3 queues behind R2, who just
    # queued behind R1), and pass 1's corridor downgrades above are
    # already reflected in `resolved` by the time this starts.
    changed = True
    while changed:
        changed = False
        final_position = {n: next_position(n) for n in names}
        for name in names:
            if resolved[name] != "move":
                continue
            robot = state.robots[name]
            np = final_position[name]
            if corridor_containing(np) is not None:
                continue  # corridor cells: pass 1's job only
            for other in names:
                if other == name:
                    continue
                other_robot = state.robots[other]
                if other_robot.direction != robot.direction:
                    continue  # different lane, no conflict possible
                if final_position[other] == np:
                    resolved[name] = "wait"
                    changed = True
                    break

    return resolved


def apply(state: WorldState, resolved: dict[str, str]) -> None:
    """Mutate `state` in place per the resolved actions - move or hold,
    nothing else. No life/urgency bookkeeping here (11a step 2)."""
    for name, action in resolved.items():
        if action != "move":
            continue
        robot = state.robots[name]
        robot.position = (robot.position + robot.direction) % PERIMETER


# --- 11a step 2: sensing, negotiation trigger, life/urgency/death --------


@dataclass
class CorridorContest:
    """One corridor's currently-decided (or deadlocked) contest, scoped to
    exactly the robots it was decided between. Retired once every
    participant has moved past this corridor, so the next arrivals
    negotiate fresh - PHASE_11_ROADMAP.md's "the winner passes through;
    then whoever is now at the front of each side re-negotiates."

    Deliberately no notion of a "winner" here, or anywhere in this module.
    Going first through one corridor decides nothing about how either
    robot is actually doing - the urgent robot going first might still be
    the one closer to death, and the one that yielded might outlast it by
    a wide margin. `goes_first` is a procedural fact (whose turn is it),
    not an outcome. The only real result this module recognizes is
    survival - see `survival_result` below, and `WorldState.died_at`."""

    participants: frozenset[str]
    goes_first: str | None  # None only when deadlocked
    deadlocked: bool = False
    entered: bool = False  # has `goes_first` actually entered the corridor
    # yet? "Not currently in_zone" alone can't tell apart a robot that
    # hasn't started moving from one that already finished - both read as
    # in_zone=False. Set True the first tick it's observed inside; only
    # after that can "not in_zone" mean "cleared," not "hasn't started."


def contender_for(state: WorldState, name: str, corridor: str) -> str | None:
    """Among robots approaching `corridor` from the OTHER direction, the
    nearest one within SENSOR_RANGE of `name`'s current position - forward,
    in `name`'s own direction of travel, matching PHASE_11_ROADMAP.md's "a
    robot at its boundary sees the whole corridor + ~4 cells past the far
    mouth" (a directional, forward-facing sensor, not an omnidirectional
    one). None if nobody is close enough yet - that robot just claims the
    corridor for free once it arrives (D12's "everything else resolves for
    free", generalized)."""
    robot = state.robots[name]
    candidates = [
        (other_name, forward_distance(robot.position, other.position, robot.direction))
        for other_name, other in state.robots.items()
        if other_name != name
        and other.direction != robot.direction
        and other.approaching_corridor == corridor
    ]
    in_range = [c for c in candidates if c[1] <= SENSOR_RANGE]
    if not in_range:
        return None
    return min(in_range, key=lambda c: c[1])[0]


def _run_negotiation(state: WorldState, name_a: str, name_b: str, max_turns: int) -> tuple[str | None, bool]:
    """A2A always has a fixed "who speaks first" convention; sorted names
    keep it deterministic here, matching D31's reasoning for the linear
    world. Negotiates on whatever `.situation` each robot's wrapped
    negotiation.Robot already carries - loop-aware observation composition
    (real position/sensor facts, generalizing D24) is 11a step 4, not this
    one; exactly how world.py's own Phase 3 negotiated before D24 added
    observation composition after the fact. Returns (goes_first_name,
    deadlocked) - goes_first is None iff deadlocked."""
    first, second = sorted([name_a, name_b])
    outcome = negotiate(state.robots[first].robot, state.robots[second].robot, max_turns=max_turns)
    if not outcome.agreed:
        return None, True
    return outcome.agreed_on, False


def resolve_contests(state: WorldState, max_negotiation_turns: int = 6) -> None:
    """Once per tick, before any robot's move is decided: retire any
    corridor contest whose participants have all moved past it, then look
    for a fresh contest to resolve per corridor - a lone arrival claims for
    free, an arrival that senses this corridor's other-direction contender
    negotiates with them (D12's early-trigger rule: doesn't need to wait
    for an exact simultaneous arrival, generalized to per-corridor + an
    arbitrary named set instead of a/b)."""
    for corridor in CORRIDORS:
        contest = state.contests.get(corridor)
        if contest is not None and not contest.entered and contest.goes_first is not None:
            leader_state = state.robots.get(contest.goes_first)
            if leader_state is not None and leader_state.in_zone and leader_state.current_corridor == corridor:
                contest.entered = True

        if contest is not None:
            still_active = any(
                name in state.robots and (
                    (state.robots[name].in_zone and state.robots[name].current_corridor == corridor)
                    or (state.robots[name].approaching_corridor == corridor and state.robots[name].at_boundary)
                )
                for name in contest.participants
            )
            if not still_active:
                state.contests[corridor] = None
                contest = None

        if contest is not None:
            continue  # already resolved (or deadlocked) and still relevant

        arrivals = [
            name for name, r in state.robots.items()
            if r.approaching_corridor == corridor and r.at_boundary
        ]
        if not arrivals:
            continue

        name = arrivals[0]  # 11a: at most one arrival per side ever exists;
        # front-of-queue selection among multiple same-side arrivals is 11b.
        contender = contender_for(state, name, corridor)
        if contender is None:
            state.contests[corridor] = CorridorContest(participants=frozenset({name}), goes_first=name)
            continue

        goes_first, deadlocked = _run_negotiation(state, name, contender, max_negotiation_turns)
        state.contests[corridor] = CorridorContest(
            participants=frozenset({name, contender}),
            goes_first=goes_first,
            deadlocked=deadlocked,
        )


def decide_one(state: WorldState, name: str) -> str:
    """One robot's move/wait decision this tick, given whatever contests
    are currently resolved. Mirrors world.py's `_decide_one`, generalized
    from one whole-episode `priority` to per-corridor `CorridorContest`s
    that get retired and re-decided as robots keep circulating."""
    robot = state.robots[name]
    if robot.in_zone:
        return "move"  # already committed, always continue through
    if not robot.at_boundary:
        return "move"  # free travel, no conflict possible yet

    corridor = robot.approaching_corridor
    contest = state.contests.get(corridor)
    if contest is None or name not in contest.participants:
        # Arrived, but resolve_contests (which always runs first in step())
        # hasn't attributed a contest to me yet - shouldn't normally happen.
        # Wait rather than guess, same defensive stance as world.py's own
        # _decide_one for the equivalent case.
        return "wait"
    if contest.deadlocked:
        return "wait"  # a standoff: neither proceeds - both then drain
    if contest.goes_first == name:
        return "move"
    leader_state = state.robots.get(contest.goes_first)
    if leader_state is None:
        cleared = True  # the other robot died before finishing - nothing left to wait for
    elif not contest.entered:
        cleared = False  # it hasn't even started its pass-through yet
    else:
        cleared = not (leader_state.in_zone and leader_state.current_corridor == corridor)
    return "move" if cleared else "wait"


def apply_drain_and_death(state: WorldState, resolved: dict[str, str]) -> list[str]:
    """Drain any robot held "wait" by a *resolved* corridor contest - not
    while unresolved or still in negotiation (negotiation itself is free -
    PHASE_11_ROADMAP.md's life/urgency section), and not a robot simply
    moving freely. A deadlock counts as resolved: both sides drain. Removes
    anyone whose life reaches 0 and returns the names removed this tick.

    Doesn't yet cover "queued behind a drained robot" (PHASE_11_ROADMAP.md's
    same-lane-following case) - moot for 11a's 2-robot starter configs,
    since a same-lane conflict can't arise with exactly one robot per
    direction; left as a documented gap for 11b's `crowd` config, not
    silently assumed away."""
    for name, action in resolved.items():
        if action != "wait":
            continue
        robot = state.robots[name]
        if not robot.at_boundary:
            continue
        corridor = robot.approaching_corridor
        contest = state.contests.get(corridor)
        if contest is None or name not in contest.participants:
            continue  # unresolved or still negotiating: free
        robot.life -= robot.urgency

    dead = [name for name, r in state.robots.items() if r.life <= 0]
    for name in dead:
        del state.robots[name]
    return dead


def step(state: WorldState, max_negotiation_turns: int = 6) -> dict:
    """Advance the world by one tick: resolve any pending contests, decide
    each robot's move, the reactive layer double-checks, positions apply,
    then life/urgency/death bookkeeping - in that order, so drain reflects
    what actually happened this tick (post `reactive_filter`), not merely
    what the executive layer proposed. No episode terminus - this is
    called in a loop by a driver (11a step 5's `loop_simulate.py`) against
    a fixed tick budget, not until anyone "reaches a target"; there is no
    target on a loop."""
    state.tick += 1
    resolve_contests(state, max_negotiation_turns)
    proposed = {name: decide_one(state, name) for name in state.robots}
    resolved = reactive_filter(state, proposed)
    apply(state, resolved)
    dead = apply_drain_and_death(state, resolved)
    for name in dead:
        state.died_at[name] = state.tick
    return {"resolved": resolved, "dead": dead}


def survival_result(state: WorldState) -> dict[str, int]:
    """How long each robot has survived, in ticks - the only notion of a
    "result" this module has, per this session's design review: going
    first through a corridor isn't a win, and the concept of a winner was
    deliberately dropped from `CorridorContest`. A robot still alive has
    survived at least `state.tick` ticks (and counting); one that died is
    recorded at the tick it died (`WorldState.died_at`). Callers compare
    these numbers themselves - this function only reports, it doesn't
    declare anyone a winner."""
    result = dict(state.died_at)
    for name in state.robots:
        result[name] = state.tick
    return result
