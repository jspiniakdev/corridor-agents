#!/usr/bin/env python3
"""Phase 6: the world as a real MCP server. See docs/PLAN.md §4.2, D17.

This is the reactive layer plus ground truth, and nothing else - the
executive layer (whose turn it is, move or wait) lives in agent.py, since
that's a decision only a robot's own process can honestly make once
robots are real, separate processes. This server never decides who goes
first and never talks to the other robot's process at all; it only
validates whatever a robot proposes and applies it.

Reuses world.py's WorldState/RobotState/reactive_filter/apply directly -
this is the exact same safety-critical logic simulate.py already uses and
already has tests for, not a reimplementation. simulate.py's own
single-process path is completely untouched; this server exists purely to
give the networked path (agent.py) somewhere to ask "where am I, what can
I sense" and "is this move safe."

No independent clock. Each robot commits its own action - move or wait -
whenever its own control loop decides to, via propose_action(). That call
resolves synchronously against reactive_filter and the world's CURRENT
ground truth (the other robot's action is fixed as "wait" for that check,
since only one robot is transitioning per call - there's no such thing as
"simultaneous" once actions are committed one at a time, so the
pathological both-enter-at-once case this project worried about in Phase
3 structurally can't arise here). The response tells the robot the real,
authoritative outcome - accepted or not, and its real position - since
position only means anything in the world's own coordinate system (a
robot's own guess is provisional until the world confirms it).

    python world_server.py --port 9500
"""

import argparse
import sys

sys.path.insert(0, "src")

from negotiation import Robot  # noqa: E402
from observation import distance_to_entrance  # noqa: E402
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
    SENSOR_RANGE,
    RobotState,
    WorldState,
    apply,
    reactive_filter,
)

from mcp.server.mcpserver import MCPServer  # noqa: E402


def build_state() -> WorldState:
    """A placeholder Robot per side, same pattern agent.py's `other` uses -
    RobotState.robot is a required field, but reactive_filter/apply never
    read it, only .position/.direction/.in_zone."""
    a = RobotState(Robot("Robot A", "", 0), A_START, A_DIRECTION, A_BOUNDARY, A_TARGET)
    b = RobotState(Robot("Robot B", "", 0), B_START, B_DIRECTION, B_BOUNDARY, B_TARGET)
    return WorldState(a, b)


STATE = build_state()

mcp = MCPServer("corridor-world")


def _robot_state(side: str) -> RobotState:
    return STATE.a if side == "a" else STATE.b


def _other_state(side: str) -> RobotState:
    return STATE.b if side == "a" else STATE.a


@mcp.tool()
def get_map() -> dict:
    """Static grid facts. Called once, at startup."""
    return {
        "min_position": 1,
        "max_position": 8,
        "corridor_zone": sorted(CORRIDOR_ZONE),
        "a_boundary": A_BOUNDARY,
        "b_boundary": B_BOUNDARY,
        "a_target": A_TARGET,
        "b_target": B_TARGET,
    }


@mcp.tool()
def get_observation(side: str) -> dict:
    """Job 4 from D4b, position/sensor half only - the private situation
    text half stays local to agent.py (D16's for_side())."""
    me = _robot_state(side)
    other = _other_state(side)
    gap = abs(me.position - other.position)
    sensed = gap <= SENSOR_RANGE
    return {
        "position": me.position,
        "distance_to_entrance": distance_to_entrance(me.position, me.direction, CORRIDOR_ZONE),
        "sensed_other": sensed,
        "gap_if_sensed": gap if sensed else None,
        "at_boundary": me.at_boundary,
        "in_zone": me.in_zone,
        "reached_target": me.reached_target,
        "other_cleared_zone": other.cleared_zone,
    }


@mcp.tool()
def propose_action(side: str, action: str) -> dict:
    """One robot commits an action - move or wait. Validated synchronously
    against reactive_filter (unchanged from world.py) using CURRENT ground
    truth, and applied immediately if safe. The other robot's action is
    fixed as "wait" for this check - only one robot transitions per call,
    so there's nothing for it to be racing against.

    Returns the real outcome: accepted (was "move" actually honored, or
    downgraded to "wait" by the safety check) and this robot's real
    position afterward - the robot's own belief was only ever provisional.
    """
    if action not in ("move", "wait"):
        raise ValueError('action must be "move" or "wait"')

    if side == "a":
        resolved, _ = reactive_filter(STATE, action, "wait")
        apply(STATE, resolved, "wait")
        position = STATE.a.position
    else:
        _, resolved = reactive_filter(STATE, "wait", action)
        apply(STATE, "wait", resolved)
        position = STATE.b.position

    STATE.log.append((side, action, resolved, STATE.a.position, STATE.b.position))
    return {"accepted": resolved == action, "actual_position": position}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=9500)
    args = parser.parse_args()

    print(f"world server listening on http://127.0.0.1:{args.port}")
    mcp.run(transport="streamable-http", host="127.0.0.1", port=args.port, stateless_http=True)


if __name__ == "__main__":
    main()
