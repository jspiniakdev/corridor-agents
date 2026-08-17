#!/usr/bin/env python3
"""Phase 3: run one grid episode and print it, tick by tick.

    python simulate.py                              # deliberate, deterministic policies
    python simulate.py --no-deliberate               # FCFS baseline - no negotiation at all
    python simulate.py --a llm --b llm               # LLM robots, needs an API key
    python simulate.py --scenario routine_vs_medical --a llm --b never_yield

Deterministic policies need no API key. The LLM policy needs:
    pip install anthropic
    export ANTHROPIC_API_KEY=sk-ant-...
"""

import argparse
import sys

sys.path.insert(0, "src")

from negotiation import POLICIES  # noqa: E402
from scenarios import BY_ID, SCENARIOS  # noqa: E402
from world import run_episode  # noqa: E402


def main() -> int:
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

    print(f"scenario: {scenario.id}")
    print(f"  Robot A ({args.a}): {scenario.a_situation}")
    print(f"  Robot B ({args.b}): {scenario.b_situation}")
    print(f"  mode: {'deliberate' if args.deliberate else 'FCFS (no negotiation)'}")
    print("-" * 72)

    result = run_episode(
        scenario,
        args.a,
        args.b,
        deliberate=args.deliberate,
        max_ticks=args.max_ticks,
        max_negotiation_turns=args.max_negotiation_turns,
    )

    for tick, a_position, b_position, a_action, b_action in result.log:
        print(f"  tick {tick}: A at {a_position} ({a_action:4}), B at {b_position} ({b_action:4})")

    print("-" * 72)
    if result.negotiation is not None:
        if result.negotiation.agreed:
            print(f"negotiation: agreed on {result.negotiation.agreed_on} ({result.negotiation.messages_used} messages)")
        else:
            print(f"negotiation: DEADLOCK ({result.negotiation.messages_used} messages)")
    else:
        print("negotiation: never needed (resolved by arrival order)")

    if result.completed:
        print(f"completed in {result.ticks_used} ticks")
    else:
        print(f"DID NOT COMPLETE after {result.ticks_used} ticks")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
