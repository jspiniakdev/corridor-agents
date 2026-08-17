"""Tests for the Phase 3 world_eval.py batch sweep. Zero API calls - only
deterministic policies are ever exercised here.
"""

import sys

sys.path.insert(0, ".")
sys.path.insert(0, "src")

from scenarios import BY_ID  # noqa: E402
from world_eval import expand_cases, read_cases, run_world_episode  # noqa: E402


def test_deadlock_produces_no_priority_and_not_completed():
    scenario = BY_ID["dying_battery_vs_fragile_cargo"]  # should_go_first == "Robot A"
    row = run_world_episode(scenario, "never_yield", "never_yield", deliberate=True, repeat=0)
    assert row["completed"] is False
    assert row["negotiated"] is True
    assert row["priority"] is None
    assert row["correct"] is None


def test_fcfs_row_is_never_negotiated():
    scenario = BY_ID["dying_battery_vs_fragile_cargo"]
    row = run_world_episode(scenario, "stubborn", "stubborn", deliberate=False, repeat=0)
    assert row["negotiated"] is False
    assert row["completed"] is True
    assert row["priority"] == "Robot A"  # FCFS: A structurally reaches its boundary first


def test_read_cases_finds_the_expected_row_counts():
    cases = read_cases("experiments/world_cases.csv")

    deliberate_deterministic = []
    fcfs = []
    deliberate_llm = []
    for case in cases:
        uses_llm = case["policy_a"] == "llm" or case["policy_b"] == "llm"
        if not case["deliberate"]:
            fcfs.append(case)
        elif uses_llm:
            deliberate_llm.append(case)
        else:
            deliberate_deterministic.append(case)

    assert len(deliberate_deterministic) == 45
    assert len(fcfs) == 5
    assert len(deliberate_llm) == 35


def test_expand_cases_without_full_excludes_deliberate_llm_but_keeps_fcfs():
    cases = [
        {"scenario_id": "s", "policy_a": "llm", "policy_b": "llm", "deliberate": True, "repeats": 3},
        {"scenario_id": "s", "policy_a": "llm", "policy_b": "llm", "deliberate": False, "repeats": 1},
        {"scenario_id": "s", "policy_a": "stubborn", "policy_b": "stubborn", "deliberate": True, "repeats": 1},
    ]
    episodes = expand_cases(cases, full=False)

    # Only the non-llm deliberate row and the FCFS llm-named row survive -
    # FCFS never negotiates, so a policy name there costs nothing.
    assert len(episodes) == 2
    deliberate_values = [deliberate for _, _, _, deliberate, _ in episodes]
    assert True in deliberate_values
    assert False in deliberate_values


def test_expand_cases_with_full_includes_everything():
    cases = [
        {"scenario_id": "s", "policy_a": "llm", "policy_b": "llm", "deliberate": True, "repeats": 3},
        {"scenario_id": "s", "policy_a": "stubborn", "policy_b": "stubborn", "deliberate": True, "repeats": 1},
    ]
    episodes = expand_cases(cases, full=True)
    assert len(episodes) == 3 + 1
