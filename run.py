#!/usr/bin/env python3
"""Run one corridor negotiation and print it.

    python run.py                               # deterministic, free, instant
    python run.py --a llm --b llm               # both robots use Claude
    python run.py --a llm --b never_yield       # LLM against an immovable peer
    python run.py --scenario routine_vs_medical --a llm --b llm

Deterministic policies need no API key. The LLM policy needs:
    pip install anthropic
    export ANTHROPIC_API_KEY=sk-ant-...
"""

import argparse
import sys

sys.path.insert(0, "src")

from negotiation import POLICIES, Robot, negotiate  # noqa: E402
from scenarios import BY_ID, SCENARIOS  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scenario", default=SCENARIOS[0].id, choices=list(BY_ID))
    parser.add_argument("--a", default="stubborn", choices=list(POLICIES), help="Robot A's policy")
    parser.add_argument("--b", default="stubborn", choices=list(POLICIES), help="Robot B's policy")
    parser.add_argument("--max-turns", type=int, default=6)
    args = parser.parse_args()

    scenario = BY_ID[args.scenario]
    a = Robot("Robot A", scenario.a_situation, scenario.a_urgency, POLICIES[args.a]())
    b = Robot("Robot B", scenario.b_situation, scenario.b_urgency, POLICIES[args.b]())

    print(f"scenario: {scenario.id}")
    print(f"  Robot A ({args.a}, urgency {a.urgency}): {a.situation}")
    print(f"  Robot B ({args.b}, urgency {b.urgency}): {b.situation}")
    print("-" * 72)

    outcome = negotiate(a, b, max_turns=args.max_turns)
    for msg in outcome.history:
        print(f"  {msg}")

    print("-" * 72)
    if outcome.agreed:
        verdict = "?" if scenario.should_go_first is None else (
            "correct" if outcome.agreed_on == scenario.should_go_first else "WRONG robot"
        )
        print(f"agreed: {outcome.agreed_on} goes first  ({verdict})")
    else:
        print("NO AGREEMENT - deadlock")
    print(f"messages: {outcome.messages_used}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
