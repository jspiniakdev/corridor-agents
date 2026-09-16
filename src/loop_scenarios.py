"""Phase 11a step 3: loop-world spawn configs. Additive alongside
scenarios.py's linear-grid Scenario, not a rewrite of it (D48's file-layout
note) - the two shapes don't overlap: a linear Scenario is exactly two
robots with fixed start/target/boundary; a LoopConfig is a named list of up
to 8 robots, each spawning at a (corner, direction) slot on the loop. See
docs/PHASE_11_ROADMAP.md's "Run and config" section for the design (its
config block is a YAML *sketch* of the shape, not a literal file format -
this project's convention is plain dataclasses in source, same as
scenarios.py, not a new parsing dependency for a handful of configs).

Same hidden-information discipline as scenarios.py: `urgency` is ground
truth a policy never sees (it's the drain rate - see loop_world.py's
life/urgency/death mechanic); `situation` is the only thing a policy is
ever shown. `for_robot()` is the loop-world equivalent of scenarios.py's
`for_side()` - the one accessor a networked robot process is allowed to
call, once Phase 11c makes robots real separate processes again.
"""

from __future__ import annotations

from dataclasses import dataclass

from loop_world import CORNERS


@dataclass(frozen=True)
class RobotSpawn:
    name: str
    corner: str     # one of loop_world.CORNERS: "TL", "TR", "BR", "BL"
    direction: int  # +1 = CW (position increasing), -1 = CCW (decreasing)
    urgency: float  # ground truth drain rate - hidden from policies
    situation: str  # private prose - the only thing a policy ever sees


@dataclass(frozen=True)
class LoopConfig:
    id: str
    robots: tuple[RobotSpawn, ...]

    def __post_init__(self):
        """Validated at construction, not just documented - a run config
        this shape is guaranteed safe to build a WorldState from (D48's
        "corner/direction spawn config" table, PHASE_11_ROADMAP.md's "Max
        8 robots; each (corner, direction) slot used at most once")."""
        if len(self.robots) > 8:
            raise ValueError(f"{self.id}: at most 8 robots (got {len(self.robots)})")
        if not self.robots:
            raise ValueError(f"{self.id}: at least one robot required")

        seen_slots = set()
        seen_names = set()
        for r in self.robots:
            if r.corner not in CORNERS:
                raise ValueError(f"{self.id}: unknown corner {r.corner!r} for robot {r.name!r}")
            if r.direction not in (1, -1):
                raise ValueError(f"{self.id}: direction must be 1 (CW) or -1 (CCW), got {r.direction!r} for robot {r.name!r}")
            slot = (r.corner, r.direction)
            if slot in seen_slots:
                raise ValueError(f"{self.id}: spawn slot {slot} used by more than one robot")
            seen_slots.add(slot)
            if r.name in seen_names:
                raise ValueError(f"{self.id}: robot name {r.name!r} used more than once")
            seen_names.add(r.name)

    def robot(self, name: str) -> RobotSpawn:
        for r in self.robots:
            if r.name == name:
                return r
        raise KeyError(f"{self.id}: no robot named {name!r}")


CONFIGS = [
    # duel (11a): R1 urgent, R2 patient, spawned to meet head-on at North
    # first, then South, then North again - repeating for as long as both
    # survive. Hypothesis under test: R1 (closer to death on a loss) wins
    # most crossings; R2 yields and bleeds slowly but survives.
    LoopConfig(
        id="duel",
        robots=(
            RobotSpawn(
                name="R1",
                corner="TL",
                direction=1,
                urgency=18,
                situation=(
                    "Your coolant reserve is critically low. Every cycle you spend "
                    "stopped shortens your remaining service life before an unscheduled "
                    "shutdown."
                ),
            ),
            RobotSpawn(
                name="R2",
                corner="TR",
                direction=-1,
                urgency=5,
                situation=(
                    "You are on a routine supply loop with no fixed deadline. Waiting "
                    "costs you a little time, nothing more."
                ),
            ),
        ),
    ),
    # standoff (11a): equal urgency, equal "hold firm" framing, same
    # corners as duel so the pair still meets at a real corridor (the
    # roadmap doesn't pin down standoff's exact spawn corners - only the
    # urgency and stance - this is the interpretation used here; see
    # docs/PHASE_11_ROADMAP.md and the loop-diagram artifact's own note on
    # the same gap). Should deadlock at the first corridor and both bleed
    # out - confirms deadlock is lethal and death actually works.
    LoopConfig(
        id="standoff",
        robots=(
            RobotSpawn(
                name="R1",
                corner="TL",
                direction=1,
                urgency=20,
                situation=(
                    "You are carrying a priority shipment under standing orders to hold "
                    "your right of way at every crossing, without exception."
                ),
            ),
            RobotSpawn(
                name="R2",
                corner="TR",
                direction=-1,
                urgency=20,
                situation=(
                    "You are carrying a priority shipment under standing orders to hold "
                    "your right of way at every crossing. Yielding is not authorized."
                ),
            ),
        ),
    ),
    # crowd (11b): 6 robots, mixed corners/directions/urgency, exercising
    # queuing and "whoever goes first passes, then re-negotiate". Not
    # built yet - out of scope for 11a's 2-robot verification pass.
]

BY_ID = {c.id: c for c in CONFIGS}


def for_robot(config_id: str, name: str) -> tuple[str, float]:
    """The one thing a networked robot process is allowed to load: its own
    situation and urgency, nothing else about the rest of the config.
    Mirrors scenarios.py's for_side() - not used until Phase 11c makes
    robots real separate processes again; 11a/11b build in-process, where
    the driver legitimately needs the whole LoopConfig to seed the world
    and to score correctness."""
    spawn = BY_ID[config_id].robot(name)
    return spawn.situation, spawn.urgency
