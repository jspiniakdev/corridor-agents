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
    other robot, and whatever private context it's been given."""
    distance = distance_to_entrance(my_position, my_direction, corridor_zone)
    lines = [f"You are {distance} cell(s) from the corridor entrance."]

    gap = abs(my_position - other_position)
    if gap <= sensor_range:
        lines.append(f"Another robot is approaching from the opposite end, {gap} cell(s) away.")

    lines.append(private_text)
    return " ".join(lines)
