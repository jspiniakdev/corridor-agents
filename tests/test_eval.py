"""Tests for the Phase 2 eval harness. Zero API calls, milliseconds to run.

Only deterministic policies are exercised here, same as test_negotiation.py -
none of these tests ever call the real LLM.
"""

import sys

sys.path.insert(0, ".")
sys.path.insert(0, "src")

from eval import expand_cases, read_cases, run_episode  # noqa: E402
from scenarios import BY_ID  # noqa: E402


def test_run_episode_scores_a_known_correct_pairing():
    scenario = BY_ID["dying_battery_vs_fragile_cargo"]  # should_go_first == "Robot A"
    row = run_episode(scenario, "stubborn", "always_yield", repeat=0)
    assert row["agreed"] is True
    assert row["agreed_on"] == "Robot A"
    assert row["correct"] is True
    assert row["messages_used"] == 2


def test_deadlock_produces_correct_none_not_false():
    """Two never_yield robots never agree. No decision was made, so
    correctness is not applicable - it should be None, not False."""
    scenario = BY_ID["dying_battery_vs_fragile_cargo"]  # a real, non-tie scenario
    row = run_episode(scenario, "never_yield", "never_yield", repeat=0)
    assert row["agreed"] is False
    assert row["should_go_first"] == "Robot A"
    assert row["correct"] is None
    assert row["messages_used"] == 6


def test_tie_scenario_produces_correct_none_even_when_agreed():
    """both_trivial has equal urgency on both sides, so there is no ground
    truth to be right or wrong about, even though the robots do agree."""
    scenario = BY_ID["both_trivial"]
    row = run_episode(scenario, "stubborn", "stubborn", repeat=0)
    assert row["agreed"] is True
    assert row["should_go_first"] is None
    assert row["correct"] is None


def test_read_cases_finds_the_expected_row_counts():
    cases = read_cases("experiments/cases.csv")

    deterministic_cases = []
    llm_cases = []
    for case in cases:
        uses_llm = case["policy_a"] == "llm" or case["policy_b"] == "llm"
        if uses_llm:
            llm_cases.append(case)
        else:
            deterministic_cases.append(case)

    assert len(deterministic_cases) == 45
    assert len(llm_cases) == 35


def test_expand_cases_without_full_only_includes_deterministic_episodes():
    cases = read_cases("experiments/cases.csv")
    episodes = expand_cases(cases, full=False)

    assert len(episodes) == 45
    for scenario_id, policy_a, policy_b, repeat in episodes:
        assert policy_a != "llm"
        assert policy_b != "llm"


def test_expand_cases_with_full_adds_the_llm_episodes_repeated_three_times():
    cases = read_cases("experiments/cases.csv")
    episodes = expand_cases(cases, full=True)

    # 45 deterministic episodes (repeats=1 each) + 35 llm cases x 3 repeats each
    assert len(episodes) == 45 + 35 * 3
