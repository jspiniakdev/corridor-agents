#!/usr/bin/env python3
"""Phase 3: run every case in experiments/world_cases.csv through the grid
simulation and record the outcomes.

    python world_eval.py          # deterministic + FCFS cases only, free, instant
    python world_eval.py --full   # also runs the deliberate llm-involving cases - costs money

Reads experiments/world_cases.csv (one row per scenario/policy-pairing/
deliberate-mode/repeat-count to try). Writes one row per actual episode to
experiments/results/world_results.csv, overwriting whatever was there
before - regenerable from the cases file at any time.

FCFS (deliberate=false) rows always run regardless of --full: negotiation
never fires under FCFS, so even a row naming the "llm" policy costs nothing
and produces the same result any other policy name would.

Then prints a summary: how many episodes ran, what fraction completed
(both robots reached their targets), what fraction of the ones with a real
answer picked the correct robot, how often a genuine negotiation actually
happened (vs. priority being claimed solo), and the average ticks used.
"""

import argparse
import csv
import os
import sys

sys.path.insert(0, "src")

from scenarios import BY_ID  # noqa: E402
from world import run_episode  # noqa: E402

CASES_PATH = "experiments/world_cases.csv"
RESULTS_PATH = "experiments/results/world_results.csv"

RESULT_COLUMNS = [
    "scenario_id",
    "policy_a",
    "policy_b",
    "deliberate",
    "repeat",
    "completed",
    "negotiated",
    "priority",
    "should_go_first",
    "correct",
    "ticks_used",
]


def read_cases(path):
    """Read experiments/world_cases.csv into a plain list of dicts, one per row."""
    cases = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            cases.append(
                {
                    "scenario_id": row["scenario_id"],
                    "policy_a": row["policy_a"],
                    "policy_b": row["policy_b"],
                    "deliberate": row["deliberate"].strip().lower() == "true",
                    "repeats": int(row["repeats"]),
                }
            )
    return cases


def run_world_episode(scenario, policy_a_name, policy_b_name, deliberate, repeat):
    """Run one grid episode and return one result row as a dict."""
    result = run_episode(scenario, policy_a_name, policy_b_name, deliberate=deliberate)

    should_go_first = scenario.should_go_first
    if result.priority is None or should_go_first is None:
        # No decision was made, or this scenario has no ground-truth answer -
        # correctness isn't a meaningful question in either case.
        correct = None
    else:
        correct = result.priority == should_go_first

    return {
        "scenario_id": scenario.id,
        "policy_a": policy_a_name,
        "policy_b": policy_b_name,
        "deliberate": deliberate,
        "repeat": repeat,
        "completed": result.completed,
        "negotiated": result.negotiation is not None,
        "priority": result.priority,
        "should_go_first": should_go_first,
        "correct": correct,
        "ticks_used": result.ticks_used,
    }


def expand_cases(cases, full):
    """List the individual episodes a sweep would run, without running any
    of them. Each case's `repeats` becomes that many separate episodes.

    A deliberate case touching the "llm" policy is skipped unless `full` is
    True. A non-deliberate (FCFS) case always runs regardless of `full`,
    since negotiation never fires under FCFS - no policy is ever consulted,
    so it never costs anything no matter which policy names the row has.
    """
    episodes = []
    for case in cases:
        uses_llm = case["policy_a"] == "llm" or case["policy_b"] == "llm"
        if uses_llm and case["deliberate"] and not full:
            continue

        for repeat in range(case["repeats"]):
            episodes.append((case["scenario_id"], case["policy_a"], case["policy_b"], case["deliberate"], repeat))

    return episodes


def run_sweep(cases, full):
    """Run every episode from expand_cases and collect the result rows."""
    results = []
    for scenario_id, policy_a_name, policy_b_name, deliberate, repeat in expand_cases(cases, full):
        scenario = BY_ID[scenario_id]
        row = run_world_episode(scenario, policy_a_name, policy_b_name, deliberate, repeat)
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

    completed_count = 0
    for row in rows:
        if row["completed"]:
            completed_count += 1

    negotiated_count = 0
    for row in rows:
        if row["negotiated"]:
            negotiated_count += 1

    scored_rows = []
    for row in rows:
        if row["correct"] is not None:
            scored_rows.append(row)

    correct_count = 0
    for row in scored_rows:
        if row["correct"]:
            correct_count += 1

    total_ticks = 0
    for row in rows:
        total_ticks += row["ticks_used"]
    average_ticks = total_ticks / total

    lines = []
    lines.append(f"episodes: {total}")
    lines.append(f"completion rate: {completed_count}/{total} ({completed_count / total:.0%})")
    lines.append(f"negotiated (vs. solo-claimed): {negotiated_count}/{total} ({negotiated_count / total:.0%})")
    if scored_rows:
        lines.append(f"correctness: {correct_count}/{len(scored_rows)} ({correct_count / len(scored_rows):.0%})")
    else:
        lines.append("correctness: n/a (no scored episodes)")
    lines.append(f"avg ticks used: {average_ticks:.1f}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--full",
        action="store_true",
        help="also run the deliberate llm-involving cases - costs money and takes minutes",
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
