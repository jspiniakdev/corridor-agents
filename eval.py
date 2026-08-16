#!/usr/bin/env python3
"""Phase 2: run every case in experiments/cases.csv and record the outcomes.

    python eval.py          # deterministic cases only, free, instant
    python eval.py --full   # also runs the llm-involving cases, costs money and takes minutes

Reads experiments/cases.csv (one row per scenario/policy-pairing/repeat-count
to try). Writes one row per actual negotiation to
experiments/results/results.csv, overwriting whatever was there before - the
results are regenerable from the cases file at any time, so nothing is lost
by overwriting.

Then prints a summary: how many episodes ran, what fraction reached
agreement, what fraction of the ones with a real answer picked the correct
robot, and the average number of messages used.
"""

import argparse
import csv
import os
import sys

sys.path.insert(0, "src")

from negotiation import POLICIES, Robot, negotiate  # noqa: E402
from scenarios import BY_ID  # noqa: E402

CASES_PATH = "experiments/cases.csv"
RESULTS_PATH = "experiments/results/results.csv"

RESULT_COLUMNS = [
    "scenario_id",
    "policy_a",
    "policy_b",
    "repeat",
    "agreed",
    "agreed_on",
    "should_go_first",
    "correct",
    "messages_used",
]


def read_cases(path):
    """Read experiments/cases.csv into a plain list of dicts, one per row."""
    cases = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            cases.append(
                {
                    "scenario_id": row["scenario_id"],
                    "policy_a": row["policy_a"],
                    "policy_b": row["policy_b"],
                    "repeats": int(row["repeats"]),
                }
            )
    return cases


def run_episode(scenario, policy_a_name, policy_b_name, repeat):
    """Run one negotiation and return one result row as a dict."""
    policy_a = POLICIES[policy_a_name]()
    policy_b = POLICIES[policy_b_name]()
    robot_a = Robot("Robot A", scenario.a_situation, scenario.a_urgency, policy_a)
    robot_b = Robot("Robot B", scenario.b_situation, scenario.b_urgency, policy_b)

    outcome = negotiate(robot_a, robot_b)

    should_go_first = scenario.should_go_first
    if not outcome.agreed or should_go_first is None:
        # No decision was made, or this scenario has no ground-truth answer -
        # correctness isn't a meaningful question in either case.
        correct = None
    else:
        correct = outcome.agreed_on == should_go_first

    return {
        "scenario_id": scenario.id,
        "policy_a": policy_a_name,
        "policy_b": policy_b_name,
        "repeat": repeat,
        "agreed": outcome.agreed,
        "agreed_on": outcome.agreed_on,
        "should_go_first": should_go_first,
        "correct": correct,
        "messages_used": outcome.messages_used,
    }


def expand_cases(cases, full):
    """List the individual episodes that a sweep would run, without running
    any of them. Each case's `repeats` becomes that many separate episodes.

    Cases where either policy is "llm" are left out unless `full` is True,
    since those are the only ones that cost money and take real time. This
    is a separate step from run_sweep so it can be tested on its own,
    without ever calling the LLM.
    """
    episodes = []
    for case in cases:
        uses_llm = case["policy_a"] == "llm" or case["policy_b"] == "llm"
        if uses_llm and not full:
            continue

        for repeat in range(case["repeats"]):
            episodes.append((case["scenario_id"], case["policy_a"], case["policy_b"], repeat))

    return episodes


def run_sweep(cases, full):
    """Run every episode from expand_cases and collect the result rows."""
    results = []
    for scenario_id, policy_a_name, policy_b_name, repeat in expand_cases(cases, full):
        scenario = BY_ID[scenario_id]
        row = run_episode(scenario, policy_a_name, policy_b_name, repeat)
        results.append(row)

    return results


def write_csv(rows, path):
    """Write result rows to a CSV file. None becomes a blank cell, not the text "None"."""
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)

    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=RESULT_COLUMNS)
        writer.writeheader()
        for row in rows:
            csv_row = {}
            for column in RESULT_COLUMNS:
                value = row[column]
                csv_row[column] = "" if value is None else value
            writer.writerow(csv_row)


def summarize(rows):
    """Build the human-readable summary printed after a sweep."""
    total = len(rows)
    if total == 0:
        return "no episodes ran"

    agreed_count = 0
    for row in rows:
        if row["agreed"]:
            agreed_count += 1

    scored_rows = []
    for row in rows:
        if row["correct"] is not None:
            scored_rows.append(row)

    correct_count = 0
    for row in scored_rows:
        if row["correct"]:
            correct_count += 1

    total_messages = 0
    for row in rows:
        total_messages += row["messages_used"]
    average_messages = total_messages / total

    lines = []
    lines.append(f"episodes: {total}")
    lines.append(f"agreement rate: {agreed_count}/{total} ({agreed_count / total:.0%})")
    if scored_rows:
        lines.append(f"correctness: {correct_count}/{len(scored_rows)} ({correct_count / len(scored_rows):.0%})")
    else:
        lines.append("correctness: n/a (no scored episodes)")
    lines.append(f"avg messages used: {average_messages:.1f}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--full",
        action="store_true",
        help="also run the llm-involving cases - costs money and takes minutes",
    )
    args = parser.parse_args()

    cases = read_cases(CASES_PATH)
    rows = run_sweep(cases, args.full)
    write_csv(rows, RESULTS_PATH)

    print(f"wrote {len(rows)} rows to {RESULTS_PATH}")
    print(summarize(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
