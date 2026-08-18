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
Priority is reconstructed here, after the fact, by the simplest signal
the log actually contains: a robot "has priority" from the first entry
where it successfully moves off its own boundary position - whether that
was negotiated or claimed for free, that's the moment "who goes first"
became real. Good enough for a debugging visualization, not a claim of
being the same kind of ground truth Phase 3's live state.priority was.
"""

import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, "src")

from scenarios import BY_ID, SCENARIOS  # noqa: E402
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
    boundary alone, so no trace file was ever written."""
    if not os.path.exists(NEGOTIATION_TRACE_PATH):
        return None
    with open(NEGOTIATION_TRACE_PATH) as f:
        return history_from_list(json.load(f))


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
    priority_per_row, decided_at = compute_priority_per_row(entries, 1, grid["a_boundary"], 8, grid["b_boundary"])

    log = []
    for i, entry in enumerate(entries):
        a_action = entry["resolved"] if entry["side"] == "a" else "wait"
        b_action = entry["resolved"] if entry["side"] == "b" else "wait"
        elapsed_ms = 0 if i == 0 else round((entry["timestamp"] - entries[i - 1]["timestamp"]) * 1000)
        log.append(
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

    negotiation = None
    if messages is not None and decided_at is not None:
        agreed_on = priority_per_row[decided_at]
        negotiation = {
            "step": decided_at,
            "agreed": agreed_on is not None,
            "agreed_on": agreed_on,
            "messages": [
                {"speaker": m.speaker, "intent": m.intent.value, "goes_first": m.goes_first, "text": m.text}
                for m in messages
            ],
        }

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
                "start_position": 1,
                "target": grid["a_target"],
                "direction": 1,
            },
            "b": {
                "name": "Robot B",
                "policy": policy_b_name,
                "situation": scenario.b_situation,
                "urgency": scenario.b_urgency,
                "start_position": 8,
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
    parser.add_argument("--scenario", default=SCENARIOS[0].id, choices=list(BY_ID))
    parser.add_argument("--a", default="stubborn", help="Robot A's policy, for display only")
    parser.add_argument("--b", default="stubborn", help="Robot B's policy, for display only")
    parser.add_argument("--world-url", default="http://127.0.0.1:9500/mcp")
    args = parser.parse_args()
    asyncio.run(main_async(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
