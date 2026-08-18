"""Phase 3, Job 4 from docs/PLAN.md §4.1: computing what a robot is allowed
to see. This module takes only plain values (positions, a direction, a zone,
a sensor range) rather than importing anything from world.py - deliberately
self-contained, so it can be tested entirely on its own, with no tick-loop
machinery involved at all.

A composed observation has three parts:
  1. A position fact - always present, computed from the grid.
  2. A sensor fact - present only if the other robot is within sensor_range.
  3. Private state - the same kind of hand-authored text scenarios.py has
     always provided (battery level, cargo, deadline). This part never
     changes: only the position/sensor facts are new and computed.
"""

from __future__ import annotations


def distance_to_entrance(position: int, direction: int, corridor_zone: set[int]) -> int:
    """How many cells a robot at `position`, heading `direction`, is from the
    nearest edge of the corridor zone."""
    if direction == 1:
        entrance = min(corridor_zone)
    else:
        entrance = max(corridor_zone)
    return abs(position - entrance)


def compose_observation(
    my_position: int,
    my_direction: int,
    other_position: int,
    corridor_zone: set[int],
    sensor_range: int,
    private_text: str,
) -> str:
    """Build the observation string a robot's policy actually sees: what it
    can tell from its own position, what it can currently sense about the
    other robot, and whatever private context it's been given.

    Phase 3's single-process version - world.py's _negotiate_priority()
    calls this directly with raw positions. See
    compose_observation_from_dict for the networked (Phase 6+) equivalent,
    built from world_server.py's get_observation() dict instead."""
    distance = distance_to_entrance(my_position, my_direction, corridor_zone)
    lines = [f"You are {distance} cell(s) from the corridor entrance."]

    gap = abs(my_position - other_position)
    if gap <= sensor_range:
        lines.append(f"Another robot is approaching from the opposite end, {gap} cell(s) away.")

    lines.append(private_text)
    return " ".join(lines)


def compose_observation_from_dict(obs: dict, other_name: str, private_text: str) -> str:
    """Same job as compose_observation() (D4b, Job 4), for the networked
    flow - built from world_server.py's get_observation() dict (D17)
    instead of raw positions, since a networked robot never holds
    positions locally at all. Extended with one fact Phase 3 never needed:
    how far the other robot is from ITS OWN boundary, not just how far
    away it currently is - without this, a robot has no way to tell a
    real conflict apart from something merely sensed from a long way off.
    D21's asymmetric grid made that gap real; caught directly from a user
    reviewing a live negotiation where the LLM had no way to know the
    other robot was nowhere near the corridor at all (D24) - this
    function didn't exist before that: compose_observation() was never
    actually called anywhere in the networked path (agent.py/
    agent_executor.py), so a networked LLM negotiated on private text
    alone, with zero position or sensing awareness, since Phase 5."""
    lines = [f"You are {obs['distance_to_entrance']} cell(s) from the corridor entrance."]
    if obs["sensed_other"]:
        lines.append(f"Another robot ({other_name}) is approaching from the opposite end, {obs['gap_if_sensed']} cell(s) away.")
        if obs.get("other_distance_to_boundary") is not None:
            lines.append(
                f"{other_name} is {obs['other_distance_to_boundary']} cell(s) from its own boundary before the corridor."
            )
    lines.append(private_text)
    return " ".join(lines)
