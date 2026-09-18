"""Phase 11a step 4: loop-aware observation composition - what a robot's
policy is actually shown, computed from the loop's real geometry. A
wholly separate file from observation.py, not new functions appended
into it (D48's file-layout note, applied the same way loop_world.py and
loop_scenarios.py already were) - the linear-grid path
(world_server.py/world_store.py/agent.py) doesn't change until 11c and
needs none of this.

Deliberate departure from observation.py's own discipline: that module is
self-contained on purpose (plain values only, never imports world.py),
because the linear grid's distance math is trivial (`abs(position -
entrance)`). The loop's geometry - mod-L wraparound, per-corridor
boundary lookups, "which corridor am I actually approaching" - is real
modular arithmetic, already written and tested in loop_world.py
(test_loop_world.py). Re-deriving it independently here would risk two
implementations of the same arithmetic drifting apart; reusing
loop_world's functions directly is the safer call for something this
size.

Same three-part shape as compose_observation(): a fact about my own
position, sensor facts about others, then whatever private text I've been
given. Genuinely different from the linear grid in one way the roadmap
calls out directly: "single other_* -> a list of sensed others (ahead /
behind / at which corridor)" - on a loop, a sensed robot might be ahead of
me or behind me in my own lane (not currently meaningful for 11a's
2-robot, opposite-direction configs, but real once 11b adds same-lane
following), or genuinely opposing me toward a shared corridor - the only
case that can actually trigger a negotiation (see loop_world.py's
contender_for). The old "approaching from the opposite end" phrasing is
gone - it was true by construction on a line, false in general on a loop.
"""

from __future__ import annotations

from loop_world import PERIMETER, WorldState, arc_gap, forward_distance, next_corridor


def my_corridor_fact(state: WorldState, name: str) -> str:
    """What a robot can tell about its own position relative to the
    corridor it's in or approaching - the loop equivalent of
    observation.py's "You are N cell(s) from the corridor entrance.\""""
    robot = state.robots[name]
    if robot.in_zone:
        return f"You are inside the {robot.current_corridor} corridor, currently passing through."
    corridor, distance = next_corridor(robot.position, robot.direction)
    if distance == 0:
        return f"You are stopped at your boundary before the {corridor} corridor."
    return f"You are {distance} cell(s) from the {corridor} corridor entrance."


def sensed_others(state: WorldState, name: str, sensor_range: int) -> list[dict]:
    """Every other robot currently within sensor_range, with enough to
    reason about it: whether it's ahead/behind in my own lane or genuinely
    opposing me toward a shared corridor, the raw sensor gap, and - for an
    opposing robot that hasn't arrived yet - its own remaining distance to
    that corridor. That last fact is the loop equivalent of D23/D24's
    other_distance_to_boundary: without it a robot can't tell a real,
    close standoff from someone merely sensed from far off (the exact gap
    this project's own D47/D48 bug exploited on the linear grid)."""
    me = state.robots[name]
    facts = []
    for other_name, other in state.robots.items():
        if other_name == name:
            continue
        gap = arc_gap(me.position, other.position)
        if gap > sensor_range:
            continue

        if other.direction == me.direction:
            # Same lane: order is fixed (no passing - see
            # PHASE_11_ROADMAP.md's "Deliberately deferred"), so whichever
            # direction from me to them is shorter tells ahead vs. behind.
            ahead_distance = forward_distance(me.position, other.position, me.direction)
            relation = "ahead" if ahead_distance <= PERIMETER - ahead_distance else "behind"
            facts.append({
                "name": other_name, "relation": relation, "gap": gap,
                "corridor": None, "corridor_distance": None, "in_zone": False,
            })
        else:
            if other.in_zone:
                corridor, distance = other.current_corridor, None
            else:
                corridor, distance = next_corridor(other.position, other.direction)
            facts.append({
                "name": other_name, "relation": "opposing", "gap": gap,
                "corridor": corridor, "corridor_distance": distance, "in_zone": other.in_zone,
            })
    return facts


def _sentence(fact: dict) -> str:
    if fact["relation"] == "opposing":
        if fact["in_zone"]:
            return (
                f"{fact['name']} is approaching from the other direction, {fact['gap']} cell(s) "
                f"away, already inside the {fact['corridor']} corridor."
            )
        return (
            f"{fact['name']} is approaching from the other direction, {fact['gap']} cell(s) away, "
            f"{fact['corridor_distance']} cell(s) from the {fact['corridor']} corridor on its side."
        )
    if fact["relation"] == "ahead":
        return f"{fact['name']} is ahead of you in your lane, {fact['gap']} cell(s) away."
    return f"{fact['name']} is behind you in your lane, {fact['gap']} cell(s) away."


def compose_loop_observation(state: WorldState, name: str, sensor_range: int, private_text: str) -> str:
    """Build the observation string a robot's policy actually sees: its
    own corridor status, one sentence per currently-sensed other robot,
    then whatever private context it's been given. The loop equivalent of
    D24's live-recomposed-every-tick observation - callers (loop_simulate.py,
    11a step 5) are expected to call this fresh each tick, the same way
    agent.py recomposes me.situation from a live get_observation() call
    rather than composing it once and letting it go stale."""
    lines = [my_corridor_fact(state, name)]
    for fact in sensed_others(state, name, sensor_range):
        lines.append(_sentence(fact))
    lines.append(private_text)
    return " ".join(lines)
