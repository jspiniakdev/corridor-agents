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
import time

sys.path.insert(0, "src")

import tracing  # noqa: E402 - Phase 10b-1, no-op unless --trace calls tracing.setup()

from observation import distance_to_entrance  # noqa: E402
from world import CORRIDOR_ZONE, SENSOR_RANGE  # noqa: E402

from mcp.server.mcpserver import MCPServer  # noqa: E402
from world_store import InMemoryWorldStore, grid_facts  # noqa: E402

# The world's storage (Phase 10b-2/D38). Defaults to the in-memory
# singleton - exactly the pre-D38 behavior; main() swaps in a
# FirestoreWorldStore when --firestore is passed. Module-level so the
# @mcp.tool() functions can reach it.
_store = InMemoryWorldStore()

mcp = MCPServer("corridor-world")


@mcp.tool()
def get_map() -> dict:
    """Static grid facts. Called once, at startup."""
    return grid_facts()


@mcp.tool()
def get_observation(side: str) -> dict:
    """Job 4 from D4b, position/sensor half only - the private situation
    text half stays local to agent.py (D16's for_side()).

    other_distance_to_boundary (D23) is not new information in kind - the
    map (including both boundaries) is already public via get_map(), and
    a robot that senses the other's position could derive this itself by
    arithmetic; this just does that arithmetic once, robustly, in the one
    place that actually knows both real positions. It's what lets a robot
    judge whether the other is anywhere near ALSO being about to
    negotiate, versus sensed-but-nowhere-close."""
    with tracing.span("world.get_observation", side=side) as s:
        state = _store.get_state()
        me, other = (state.a, state.b) if side == "a" else (state.b, state.a)
        gap = abs(me.position - other.position)
        sensed = gap <= SENSOR_RANGE
        if s is not None:
            s.set_attribute("world.position", me.position)
            s.set_attribute("world.at_boundary", me.at_boundary)
            s.set_attribute("world.sensed_other", sensed)
        return {
            "position": me.position,
            "distance_to_entrance": distance_to_entrance(me.position, me.direction, CORRIDOR_ZONE),
            "sensed_other": sensed,
            "gap_if_sensed": gap if sensed else None,
            "other_distance_to_boundary": abs(other.position - other.boundary) if sensed else None,
            "other_distance_to_entrance": distance_to_entrance(other.position, other.direction, CORRIDOR_ZONE) if sensed else None,
            "at_boundary": me.at_boundary,
            "in_zone": me.in_zone,
            "reached_target": me.reached_target,
            "other_cleared_zone": other.cleared_zone,
            "episode_id": _store.current_episode_id(),  # D44: the robot tags its writes with this; a new one means reset_world() ran
        }


@mcp.tool()
def propose_action(side: str, action: str, episode_id: str | None = None, nonce: str | None = None) -> dict:
    """One robot commits an action - move or wait. Validated synchronously
    against reactive_filter (unchanged from world.py) using CURRENT ground
    truth, and applied immediately if safe. The other robot's action is
    fixed as "wait" for this check - only one robot transitions per call,
    so there's nothing for it to be racing against.

    Returns the real outcome: accepted (was "move" actually honored, or
    downgraded to "wait") and this robot's real position afterward - the
    robot's own belief was only ever provisional. A "move" is downgraded
    either by the safety check, by (D43) the "too fast" floor (a move less
    than MIN_MOVE_INTERVAL_SECONDS after this side's last accepted one -
    reason="too_fast"), or by (D44) `episode_id` not matching the current
    episode (a stray write from a finished one - reason="stale_episode").
    `nonce` (D44) makes the call idempotent: the same nonce from the same
    side returns the original outcome, nothing re-applied. All non-accepted
    cases just mean "didn't move, look again next poll".
    """
    if action not in ("move", "wait"):
        raise ValueError('action must be "move" or "wait"')

    with tracing.span("world.propose_action", side=side, action=action) as s:
        entry = _store.propose(side, action, episode_id, nonce)
        position = entry["a_position"] if side == "a" else entry["b_position"]
        if s is not None:
            s.set_attribute("world.resolved", entry["resolved"])
            s.set_attribute("world.accepted", entry["resolved"] == action)
            s.set_attribute("world.a_position", entry["a_position"])
            s.set_attribute("world.b_position", entry["b_position"])
            if "reason" in entry:
                s.set_attribute("world.reason", entry["reason"])
        result = {"accepted": entry["resolved"] == action, "actual_position": position}
        if "reason" in entry:  # D43: "too_fast" - the move was refused for pacing, not safety
            result["reason"] = entry["reason"]
        return result


@mcp.tool()
def get_log() -> dict:
    """The full history of committed actions, one entry per propose_action
    call, for whichever process wants to reconstruct what actually
    happened after the fact - see visualize_network.py, D19/D20. Not used
    by agent.py itself; this is purely for later inspection.

    Includes a real wall-clock timestamp per entry (D20) - there's no
    shared tick anymore (each entry is one robot's independent, serialized
    commit), so "how much real time actually passed between these two
    events" is only recoverable from real timestamps, not from entry
    order alone."""
    with tracing.span("world.get_log"):
        return {"entries": _store.get_log()}


@mcp.tool()
def record_negotiation(
    messages: list, comms_established_at: float, resolved_at: float, episode_id: str | None = None
) -> dict:
    """The robot that held the full negotiation transcript hands it to the
    world once the negotiation concludes (D39), so visualize_network.py
    can fetch it alongside the movement log - the same job the local
    negotiation_trace.json file does for a pure-local run, but reachable
    when the robots ran on Cloud Run. Same shape agent.py's
    write_negotiation_trace writes: messages carry a per-entry `timestamp`
    (D29).

    D44: `episode_id` must match the current episode or the write is
    refused (returns recorded=0, stale=true) - this is what stops a
    negotiation that resolved slowly, well into the *next* episode, from
    clobbering world/current (the bug that left the visualizer a
    fresh-log/stale-transcript mix). `resolved_at` is re-stamped with the
    world's own clock here - the one moment on the same clock as the
    movement log, so the replay can place the negotiation on the
    timeline without trusting a robot's wall clock. The passed value and
    `comms_established_at` stay for the local trace file's use."""
    with tracing.span("world.record_negotiation") as s:
        accepted = _store.set_negotiation(
            {"messages": messages, "comms_established_at": comms_established_at, "resolved_at": time.time()},
            episode_id,
        )
        if s is not None:
            s.set_attribute("world.accepted", accepted)
        return {"recorded": len(messages) if accepted else 0, "stale": not accepted}


@mcp.tool()
def get_negotiation() -> dict:
    """The transcript recorded by record_negotiation, or {"negotiation":
    None} if this episode never negotiated (a robot arrived at its
    boundary alone). For visualize_network.py's MCP path; the Firestore
    path reads world/current.negotiation directly."""
    with tracing.span("world.get_negotiation"):
        return {"negotiation": _store.get_negotiation()}


@mcp.tool()
def reset_world() -> dict:
    """Both robots back to their start positions, log emptied, last
    negotiation transcript cleared - a fresh episode. In the in-memory
    backend this is what a server restart used to do; with --firestore
    the doc persists across restarts, so this is the explicit "new
    episode" trigger (also what a future control UI would call). See D38."""
    with tracing.span("world.reset"):
        _store.reset()
        return {"reset": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=9500)
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Phase 7: 0.0.0.0 (not 127.0.0.1) so other containers on the same Compose network can reach this one - still reachable via localhost for plain local runs too.",
    )
    parser.add_argument(
        "--trace",
        action="store_true",
        help="Phase 10b-1/D35: emit OpenTelemetry spans - world.get_observation / world.propose_action per tool call, under the incoming request's trace, so a robot's mcp.* client span and this server's handling nest into one tree. Off by default. Exporter from OTEL_TRACES_EXPORTER (console / gcp).",
    )
    parser.add_argument(
        "--firestore",
        action="store_true",
        help="Phase 10b-2/D38: keep the world's positions in a Firestore document (world/current) instead of an in-memory singleton, so it survives Cloud Run's multi-instance / recycle model. Off by default - a single-process local run keeps the in-memory store, no google-cloud-firestore import. Honors FIRESTORE_EMULATOR_HOST for local dev.",
    )
    parser.add_argument(
        "--firestore-project",
        default=None,
        help="GCP project for --firestore (default: let the SDK resolve it - ADC / GOOGLE_CLOUD_PROJECT on Cloud Run, any string with FIRESTORE_EMULATOR_HOST set locally)",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="reset the world to start positions on startup - a local convenience matching 'restart the server = fresh episode'. Do NOT pass this on a multi-instance deploy: every instance that starts would wipe the shared world. Use the reset_world() tool there instead.",
    )
    args = parser.parse_args()

    global _store
    if args.firestore:
        from world_store import FirestoreWorldStore

        _store = FirestoreWorldStore(project=args.firestore_project)
    if args.reset:
        _store.reset()

    print(f"world server listening on http://{args.host}:{args.port}" + (" (firestore)" if args.firestore else ""))
    if args.trace:
        # Build the Starlette app ourselves so it can be wrapped for
        # traceparent extraction - mcp.run() does exactly this internally
        # (streamable_http_app + uvicorn) with no seam to instrument.
        import uvicorn

        tracing.setup("world")
        app = tracing.instrument_asgi(mcp.streamable_http_app(stateless_http=True, host=args.host))
        try:
            uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
        finally:
            tracing.shutdown()
    else:
        mcp.run(transport="streamable-http", host=args.host, port=args.port, stateless_http=True)


if __name__ == "__main__":
    main()
