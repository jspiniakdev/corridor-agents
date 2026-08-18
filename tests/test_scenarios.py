"""Tests for scenarios.py's for_side() - the one accessor a networked robot
process (agent.py) is allowed to call. See D16."""

import sys

sys.path.insert(0, "src")

from scenarios import SCENARIOS, for_side  # noqa: E402


def test_for_side_a_returns_only_a_situation_and_urgency():
    scenario = SCENARIOS[0]
    situation, urgency = for_side(scenario.id, "a")
    assert situation == scenario.a_situation
    assert urgency == scenario.a_urgency


def test_for_side_b_returns_only_b_situation_and_urgency():
    scenario = SCENARIOS[0]
    situation, urgency = for_side(scenario.id, "b")
    assert situation == scenario.b_situation
    assert urgency == scenario.b_urgency
