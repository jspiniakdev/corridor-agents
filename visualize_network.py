#!/usr/bin/env python3
"""Phase 6 follow-on: build an HTML replay of a networked episode, reusing
visualize_template.html unchanged. See docs/PLAN.md §5, D19.

Run this AFTER a live networked episode has finished (world_server.py +
two agent.py --world-url processes) - it's a post-hoc tool, not part of
the live run:

    python visualize_network.py --scenario dying_battery_vs_fragile_cargo --a stubborn --b always_yield --world-url http://127.0.0.1:9500/mcp

It fetches the completed episode's log from world_server.py over MCP
(get_map/get_log), reads experiments/results/negotiation_trace.json if a
negotiation happened (nothing to read if a robot arrived at its boundary
alone), and replays them into the exact episode_data shape Phase 3's
visualize.py produced - the template needs zero changes.

world_server.py's log has no concept of "priority" at all (D17) - that
decision lives entirely in agent.py/A2A now, never touching the world.
When a negotiation happened, the winner comes straight from
negotiation.check_agreement() (authoritative - it's the same function
that decided it live) and *when* it happened comes from real timestamps
(D22): agent.py now records comms_established_at (when the negotiating
task actually opened) and resolved_at (when it concluded) in the trace
file, correlated here against world_server.py's own per-entry timestamps
(D20) to find the matching world-log step. This replaces an earlier,
broken heuristic ("priority becomes known when the winner first moves
off its own boundary") that looked right on a symmetric grid but badly
misattributed the negotiation's timing once the grid became asymmetric
(D21) - a robot can win a negotiation and then still be many, unrelated
steps away from ever reaching its own boundary. The boundary-crossing
signal is kept only as a fallback, for episodes that never negotiate at
all (a robot arrived alone) - there both a real winner and a real "when"
have to be inferred from the log, since there's no trace file to read.
"""

import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, "src")

from negotiation import check_agreement  # noqa: E402
from scenarios import BY_ID  # noqa: E402
from wire import history_from_list  # noqa: E402

from agent import NEGOTIATION_TRACE_PATH  # noqa: E402

TEMPLATE_PATH = "visualize_template.html"
OUTPUT_PATH = "experiments/results/visualization_network.html"
PLACEHOLDER = "/*__EPISODE_DATA__*/"


async def mcp_call(client, name, **args):
    result = await client.call_tool(name, args)
    return json.loads(result.content[0].text)


def load_negotiation_trace():
    """None if the episode never negotiated - a robot arrived at its
    boundary alone, so no trace file was ever written. Otherwise
    {"messages": [Message, ...], "comms_established_at": float,
    "resolved_at": float} - see D22."""
    if not os.path.exists(NEGOTIATION_TRACE_PATH):
        return None
    with open(NEGOTIATION_TRACE_PATH) as f:
        data = json.load(f)
    return {
        "messages": history_from_list(data["messages"]),
        "comms_established_at": data["comms_established_at"],
        "resolved_at": data["resolved_at"],
    }


def find_step_at_or_after(entries, target_timestamp):
    """The first world-log entry whose real timestamp is >= target_timestamp
    - i.e., the world-log step that was happening around the time this
    real negotiation-channel event occurred (D22). Falls back to the last
    entry if the target is after everything the world ever logged (e.g.
    the negotiation resolved right as the episode was ending)."""
    if not entries:
        return None
    for i, entry in enumerate(entries):
        if entry["timestamp"] >= target_timestamp:
            return i
    return len(entries) - 1


def compute_priority_per_row(entries, a_start, a_boundary, b_start, b_boundary):
    """See the module docstring - priority becomes known (and locked in)
    at the first row where a robot successfully moves off its own
    boundary position. Returns (priority_per_row, decided_at_index)."""
    priority = None
    decided_at = None
    prev_a, prev_b = a_start, b_start
    per_row = []
    for i, entry in enumerate(entries):
        if priority is None:
            if entry["side"] == "a" and entry["resolved"] == "move" and prev_a == a_boundary:
                priority, decided_at = "Robot A", i
            elif entry["side"] == "b" and entry["resolved"] == "move" and prev_b == b_boundary:
                priority, decided_at = "Robot B", i
        per_row.append(priority)
        prev_a, prev_b = entry["a_position"], entry["b_position"]
    return per_row, decided_at


def collapse_idle_runs(raw_log):
    """Consecutive rows where neither robot moved and priority didn't
    change get merged into one displayed row, summing elapsed_ms.

    Every propose_action call logs a row, including no-op "wait" polls
    (world_server.py's get_log() docstring), and each robot polls on its
    own ~0.2s loop regardless of what the other is doing. LLMPolicy.respond()
    is a synchronous call inside an async def (a pre-existing limitation,
    D15) - it blocks that robot's *entire* process for the duration of an
    API call, so while one robot is mid-negotiation the other keeps
    polling and logging identical "wait" rows the whole time. A real
    8-second, 3-turn negotiation can end up looking like it took 40+
    steps, with dozens of frames of nothing happening in between - not
    wrong (D20's real elapsed-time replay is accurate), just noisy to
    watch. Collapsing these into one frame per idle stretch (labeled with
    how many polls it absorbed, so nothing is silently hidden) keeps the
    replay honest without forcing a viewer through every poll.

    Returns (collapsed_rows, old_step_to_new_step) - the mapping lets
    negotiation.step/comms_step (computed against the raw, uncollapsed
    rows via find_step_at_or_after) point at the right collapsed row."""
    collapsed = []
    old_to_new = {}
    i = 0
    n = len(raw_log)
    while i < n:
        row = raw_log[i]
        is_idle = row["a_action"] == "wait" and row["b_action"] == "wait"
        run_end = i + 1
        if is_idle:
            while run_end < n:
                other = raw_log[run_end]
                if (
                    other["a_action"] == "wait"
                    and other["b_action"] == "wait"
                    and other["a_position"] == row["a_position"]
                    and other["b_position"] == row["b_position"]
                    and other["priority"] == row["priority"]
                ):
                    run_end += 1
                else:
                    break
        new_step = len(collapsed)
        for old_index in range(i, run_end):
            old_to_new[old_index] = new_step
        collapsed.append(
            {
                "step": new_step,
                "a_position": row["a_position"],
                "b_position": row["b_position"],
                "a_action": row["a_action"],
                "b_action": row["b_action"],
                "priority": row["priority"],
                "elapsed_ms": sum(raw_log[k]["elapsed_ms"] for k in range(i, run_end)),
                "idle_polls_collapsed": run_end - i,
            }
        )
        i = run_end
    return collapsed, old_to_new


def build_episode_data(scenario, policy_a_name, policy_b_name, grid, entries, messages):
    """Same shape build_episode_data() produced in Phase 3, with one
    honest change (D20): "tick" is gone. There's no shared clock anymore
    - each entry is one robot's independent, serialized commit, arriving
    whenever its own async loop called in - so a synchronized-looking
    tick number would just be lying about how much (or how little) real
    time passed between two entries. "step" replaces it as a plain
    sequence index, and elapsed_ms (from world_server.py's real
    timestamps, D20) is the actual signal for how far apart two events
    really were - two robots moving milliseconds apart, or a multi-second
    negotiation gap, now look different in the replay instead of both
    just being "the next tick."."""
    if messages is None:
        # never negotiated - a robot arrived at its boundary alone, so the
        # only signal available at all is the log itself
        priority_per_row, decided_at = compute_priority_per_row(
            entries, grid["min_position"], grid["a_boundary"], grid["max_position"], grid["b_boundary"]
        )
        negotiation = None
    else:
        # a real negotiation happened - the winner and its real timing
        # both come from the trace file (D22), not inferred from movement
        agreed_on = check_agreement(messages["messages"])
        resolved_at = messages["resolved_at"]
        comms_at = messages["comms_established_at"]
        decided_at = find_step_at_or_after(entries, resolved_at)
        comms_step = find_step_at_or_after(entries, comms_at)
        priority_per_row = [None] * len(entries)
        if decided_at is not None:
            for i in range(decided_at, len(entries)):
                priority_per_row[i] = agreed_on
        negotiation = {
            "step": decided_at,
            "comms_step": comms_step,
            "agreed": agreed_on is not None,
            "agreed_on": agreed_on,
            "messages": [
                {"speaker": m.speaker, "intent": m.intent.value, "goes_first": m.goes_first, "text": m.text}
                for m in messages["messages"]
            ],
        }

    raw_log = []
    a_seen = False
    b_seen = False
    for i, entry in enumerate(entries):
        # The world log has one row per propose_action call, from whichever
        # side made it (D17/D20) - a robot that hasn't logged its own first
        # call yet has no row to speak of at all, not a "wait" decision.
        # Without this distinction, a robot whose process simply started a
        # beat later than its peer's (an ordinary side effect of how two
        # separate OS processes get launched, not a real negotiation delay)
        # looks indistinguishable from one that reached its boundary and
        # deliberately chose to hold - caught directly from a user reading
        # a replay and asking why Robot A appeared "stuck" at the start
        # when nothing in decide_movement() should have blocked it (D28).
        if entry["side"] == "a":
            a_action = entry["resolved"]
            a_seen = True
        else:
            a_action = "wait" if a_seen else None
        if entry["side"] == "b":
            b_action = entry["resolved"]
            b_seen = True
        else:
            b_action = "wait" if b_seen else None
        elapsed_ms = 0 if i == 0 else round((entry["timestamp"] - entries[i - 1]["timestamp"]) * 1000)
        raw_log.append(
            {
                "step": i,
                "a_position": entry["a_position"],
                "b_position": entry["b_position"],
                "a_action": a_action,
                "b_action": b_action,
                "priority": priority_per_row[i],
                "elapsed_ms": elapsed_ms,
            }
        )

    log, old_to_new_step = collapse_idle_runs(raw_log)
    if negotiation is not None:
        negotiation["step"] = None if negotiation["step"] is None else old_to_new_step[negotiation["step"]]
        negotiation["comms_step"] = None if negotiation["comms_step"] is None else old_to_new_step[negotiation["comms_step"]]

    final_priority = priority_per_row[-1] if priority_per_row else None
    should_go_first = scenario.should_go_first
    correct = None if final_priority is None or should_go_first is None else final_priority == should_go_first
    reached_target = bool(entries) and entries[-1]["a_position"] == grid["a_target"] and entries[-1]["b_position"] == grid["b_target"]

    return {
        "scenario_id": scenario.id,
        "grid": {
            "min_position": grid["min_position"],
            "max_position": grid["max_position"],
            "corridor_zone": grid["corridor_zone"],
            "a_boundary": grid["a_boundary"],
            "b_boundary": grid["b_boundary"],
        },
        "robots": {
            "a": {
                "name": "Robot A",
                "policy": policy_a_name,
                "situation": scenario.a_situation,
                "urgency": scenario.a_urgency,
                "start_position": grid["min_position"],
                "target": grid["a_target"],
                "direction": 1,
            },
            "b": {
                "name": "Robot B",
                "policy": policy_b_name,
                "situation": scenario.b_situation,
                "urgency": scenario.b_urgency,
                "start_position": grid["max_position"],
                "target": grid["b_target"],
                "direction": -1,
            },
        },
        "log": log,
        "negotiation": negotiation,
        "completed": reached_target,
        "steps_used": len(entries),
        "priority": final_priority,
        "should_go_first": should_go_first,
        "correct": correct,
    }


def render_html(template_text, episode_data):
    if PLACEHOLDER not in template_text:
        raise ValueError(f"{TEMPLATE_PATH} is missing the {PLACEHOLDER} marker")
    return template_text.replace(PLACEHOLDER, json.dumps(episode_data, indent=2))


async def main_async(args):
    from mcp.client import Client

    scenario = BY_ID[args.scenario]
    async with Client(args.world_url) as world:
        grid = await mcp_call(world, "get_map")
        log_result = await mcp_call(world, "get_log")

    entries = log_result["entries"]
    messages = load_negotiation_trace()
    episode_data = build_episode_data(scenario, args.a, args.b, grid, entries, messages)

    with open(TEMPLATE_PATH) as f:
        template_text = f.read()
    html = render_html(template_text, episode_data)

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        f.write(html)
    print(f"wrote {OUTPUT_PATH} - open it directly in a browser")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scenario", default="routine_vs_medical", choices=list(BY_ID))
    parser.add_argument("--a", default="llm", help="Robot A's policy, for display only")
    parser.add_argument("--b", default="llm", help="Robot B's policy, for display only")
    parser.add_argument("--world-url", default="http://127.0.0.1:9500/mcp")
    args = parser.parse_args()
    asyncio.run(main_async(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
