#!/usr/bin/env python3
"""Phase 3: build a self-contained HTML replay of one grid episode.

    python visualize.py
    python visualize.py --scenario routine_vs_medical --a llm --b never_yield
    python visualize.py --no-deliberate

Runs world.run_episode exactly once (same args as simulate.py), builds a
JSON-serializable snapshot of the episode, and writes it into
visualize_template.html's placeholder to produce
experiments/results/visualization.html. Open that file directly in a
browser - no server needed, the episode data is inlined into the page.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, "src")

from negotiation import POLICIES  # noqa: E402
from scenarios import BY_ID, SCENARIOS  # noqa: E402
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
    run_episode,
)

TEMPLATE_PATH = "visualize_template.html"
OUTPUT_PATH = "experiments/results/visualization.html"
PLACEHOLDER = "/*__EPISODE_DATA__*/"


def build_episode_data(scenario, policy_a_name, policy_b_name, result):
    """Turn an already-run EpisodeResult into the JSON-serializable snapshot
    the template's JS needs to animate. No I/O here - this is the pure,
    testable part."""
    negotiation = None
    if result.negotiation is not None:
        messages = []
        for message in result.negotiation.history:
            messages.append(
                {
                    "speaker": message.speaker,
                    "intent": message.intent.value,
                    "goes_first": message.goes_first,
                    "text": message.text,
                }
            )
        negotiation = {
            "tick": result.negotiation_tick,
            "agreed": result.negotiation.agreed,
            "agreed_on": result.negotiation.agreed_on,
            "messages": messages,
        }

    should_go_first = scenario.should_go_first
    if result.priority is None or should_go_first is None:
        # No decision was made, or this scenario has no ground-truth answer -
        # correctness isn't a meaningful question in either case. Same rule
        # world_eval.py already uses.
        correct = None
    else:
        correct = result.priority == should_go_first

    log = []
    for tick, a_position, b_position, a_action, b_action, priority in result.log:
        log.append(
            {
                "tick": tick,
                "a_position": a_position,
                "b_position": b_position,
                "a_action": a_action,
                "b_action": b_action,
                "priority": priority,
            }
        )

    return {
        "scenario_id": scenario.id,
        "grid": {
            "min_position": 1,
            "max_position": 8,
            "corridor_zone": sorted(CORRIDOR_ZONE),
            "a_boundary": A_BOUNDARY,
            "b_boundary": B_BOUNDARY,
        },
        "robots": {
            "a": {
                "name": "Robot A",
                "policy": policy_a_name,
                "situation": scenario.a_situation,
                "urgency": scenario.a_urgency,
                "start_position": A_START,
                "target": A_TARGET,
                "direction": A_DIRECTION,
            },
            "b": {
                "name": "Robot B",
                "policy": policy_b_name,
                "situation": scenario.b_situation,
                "urgency": scenario.b_urgency,
                "start_position": B_START,
                "target": B_TARGET,
                "direction": B_DIRECTION,
            },
        },
        "log": log,
        "negotiation": negotiation,
        "completed": result.completed,
        "ticks_used": result.ticks_used,
        "priority": result.priority,
        "should_go_first": should_go_first,
        "correct": correct,
    }


def render_html(template_text, episode_data):
    """Plain string substitution - no templating engine. Raises if the
    placeholder is missing, so a template edit that drops the marker fails
    loudly instead of silently shipping a page with no data."""
    if PLACEHOLDER not in template_text:
        raise ValueError(f"{TEMPLATE_PATH} is missing the {PLACEHOLDER} marker")
    return template_text.replace(PLACEHOLDER, json.dumps(episode_data, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scenario", default=SCENARIOS[0].id, choices=list(BY_ID))
    parser.add_argument("--a", default="stubborn", choices=list(POLICIES), help="Robot A's policy")
    parser.add_argument("--b", default="stubborn", choices=list(POLICIES), help="Robot B's policy")
    parser.add_argument(
        "--deliberate",
        dest="deliberate",
        action="store_true",
        default=True,
        help="allow negotiation on a genuine standoff (default)",
    )
    parser.add_argument(
        "--no-deliberate",
        dest="deliberate",
        action="store_false",
        help="FCFS baseline - never negotiate, a genuine tie goes to Robot A",
    )
    parser.add_argument("--max-ticks", type=int, default=30)
    parser.add_argument("--max-negotiation-turns", type=int, default=6)
    args = parser.parse_args()

    scenario = BY_ID[args.scenario]
    result = run_episode(
        scenario,
        args.a,
        args.b,
        deliberate=args.deliberate,
        max_ticks=args.max_ticks,
        max_negotiation_turns=args.max_negotiation_turns,
    )
    episode_data = build_episode_data(scenario, args.a, args.b, result)

    with open(TEMPLATE_PATH) as f:
        template_text = f.read()
    html = render_html(template_text, episode_data)

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        f.write(html)

    print(f"wrote {OUTPUT_PATH} - open it directly in a browser")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
