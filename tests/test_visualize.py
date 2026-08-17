"""Tests for the Phase 3 visualizer's data preparation. Zero API calls -
only deterministic policies are used. The HTML/JS animation itself isn't
unit-tested here (no browser/JS runtime in this repo's test stack) - verify
it manually by running `python visualize.py` and opening the output file.
"""

import json
import sys

import pytest

sys.path.insert(0, ".")
sys.path.insert(0, "src")

from scenarios import BY_ID  # noqa: E402
from visualize import PLACEHOLDER, TEMPLATE_PATH, build_episode_data, render_html  # noqa: E402
from world import run_episode  # noqa: E402


def test_episode_data_is_json_serializable():
    scenario = BY_ID["dying_battery_vs_fragile_cargo"]
    result = run_episode(scenario, "stubborn", "stubborn", deliberate=True)
    data = build_episode_data(scenario, "stubborn", "stubborn", result)
    json.dumps(data)  # must not raise


def test_negotiation_present_and_tick_aligned_with_log():
    scenario = BY_ID["dying_battery_vs_fragile_cargo"]
    result = run_episode(scenario, "stubborn", "stubborn", deliberate=True)
    data = build_episode_data(scenario, "stubborn", "stubborn", result)

    assert data["negotiation"] is not None
    tick = data["negotiation"]["tick"]
    assert data["log"][tick]["tick"] == tick


def test_negotiation_absent_under_fcfs():
    scenario = BY_ID["dying_battery_vs_fragile_cargo"]
    result = run_episode(scenario, "stubborn", "stubborn", deliberate=False)
    data = build_episode_data(scenario, "stubborn", "stubborn", result)
    assert data["negotiation"] is None


def test_deadlock_produces_agreed_false_and_no_agreed_on():
    scenario = BY_ID["dying_battery_vs_fragile_cargo"]
    result = run_episode(scenario, "never_yield", "never_yield", deliberate=True)
    data = build_episode_data(scenario, "never_yield", "never_yield", result)

    assert data["negotiation"]["agreed"] is False
    assert data["negotiation"]["agreed_on"] is None
    assert data["correct"] is None  # D10: no decision, no verdict


def test_urgency_and_situation_are_present_for_the_human_viewer():
    """Deliberately the opposite invariant from observation.py's - this
    data is for a human debugging tool, not a policy prompt, so ground
    truth urgency showing up here is correct, not a leak."""
    scenario = BY_ID["dying_battery_vs_fragile_cargo"]
    result = run_episode(scenario, "stubborn", "stubborn", deliberate=False)
    data = build_episode_data(scenario, "stubborn", "stubborn", result)

    assert data["robots"]["a"]["urgency"] == scenario.a_urgency
    assert data["robots"]["a"]["situation"] == scenario.a_situation


def test_render_html_substitutes_the_placeholder_and_drops_the_marker():
    template = "before " + PLACEHOLDER + " after"
    html = render_html(template, {"x": 1})
    assert PLACEHOLDER not in html
    assert '"x": 1' in html


def test_render_html_raises_if_placeholder_missing():
    with pytest.raises(ValueError):
        render_html("no marker here", {"x": 1})


def test_real_template_file_has_the_placeholder():
    """A cheap guard against the checked-in template drifting - catches a
    stray edit that deletes the marker, without needing a browser."""
    with open(TEMPLATE_PATH) as f:
        assert PLACEHOLDER in f.read()
