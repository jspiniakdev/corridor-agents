"""Tests for the network visualizer's data preparation. Zero network calls
- entries/messages are hand-built. The HTML/JS animation itself isn't
unit-tested here (no browser/JS runtime in this repo's test stack) -
verified manually by running visualize_network.py against a live episode
and opening the output file. See D19, D21, D22.
"""

import json
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "src")

from negotiation import Intent, Message  # noqa: E402
from scenarios import BY_ID  # noqa: E402
from visualize_network import _negotiation_from_dict, build_episode_data, find_step_at_or_after  # noqa: E402

GRID = {
    "min_position": 1,
    "max_position": 24,
    "corridor_zone": [3, 4, 5],
    "a_boundary": 2,
    "b_boundary": 6,
    "a_target": 24,
    "b_target": 1,
}


def entry(side, action, resolved, a_pos, b_pos, timestamp):
    return {"side": side, "action": action, "resolved": resolved, "a_position": a_pos, "b_position": b_pos, "timestamp": timestamp}


def test_find_step_at_or_after_returns_the_first_matching_entry():
    entries = [entry("a", "wait", "wait", 1, 24, 100.0), entry("a", "move", "move", 2, 24, 101.0), entry("b", "move", "move", 2, 23, 102.0)]
    assert find_step_at_or_after(entries, 100.5) == 1
    assert find_step_at_or_after(entries, 100.0) == 0


def test_find_step_at_or_after_falls_back_to_the_last_entry_when_target_is_later_than_everything():
    entries = [entry("a", "wait", "wait", 1, 24, 100.0)]
    assert find_step_at_or_after(entries, 999.0) == 0


def test_find_step_at_or_after_returns_none_for_an_empty_log():
    assert find_step_at_or_after([], 100.0) is None


def test_negotiation_from_dict_rehydrates_messages_and_passes_timestamps_through():
    """D39: the same transform for the local trace file and the Firestore
    `negotiation` field. None in -> None out (episode never negotiated)."""
    assert _negotiation_from_dict(None) is None

    data = {
        "messages": [{"speaker": "Robot A", "intent": "propose", "goes_first": "Robot A", "text": "me first"}],
        "comms_established_at": 10.0,
        "resolved_at": 12.5,
    }
    out = _negotiation_from_dict(data)
    assert out["comms_established_at"] == 10.0 and out["resolved_at"] == 12.5
    assert out["messages"][0].speaker == "Robot A"
    assert out["messages"][0].intent is Intent.PROPOSE


def test_build_episode_data_with_a_real_negotiation_uses_trace_timestamps_not_boundary_crossing():
    # This is exactly the D22 bug: B wins but doesn't reach ITS boundary
    # (position 6) until step 4, long after comms/resolution actually
    # happened (timestamps 100/101) - the negotiation must be anchored to
    # those real timestamps, not to step 4.
    entries = [
        entry("a", "move", "move", 2, 24, 100.0),
        entry("b", "move", "move", 2, 23, 100.5),
        entry("b", "move", "move", 2, 22, 101.5),
        entry("a", "wait", "wait", 2, 22, 102.0),
        entry("b", "move", "move", 2, 6, 103.0),  # B finally reaches its boundary
        entry("b", "move", "move", 2, 5, 104.0),  # B is now allowed to cross
    ]
    messages = {
        "messages": [
            Message("Robot A", Intent.PROPOSE, "Robot B", "you go first"),
            Message("Robot B", Intent.ACCEPT, "Robot B", "confirmed"),
        ],
        "comms_established_at": 100.0,
        "resolved_at": 100.6,
    }
    scenario = BY_ID["routine_vs_medical"]

    data = build_episode_data(scenario, "llm", "llm", GRID, entries, messages)

    assert data["negotiation"]["agreed_on"] == "Robot B"
    assert data["negotiation"]["comms_step"] == 0  # entry at/after timestamp 100.0
    assert data["negotiation"]["step"] == 2  # entry at/after timestamp 100.6, NOT step 4
    # priority is known from that resolved step onward, well before B's own boundary crossing
    assert data["log"][2]["priority"] == "Robot B"
    assert data["log"][3]["priority"] == "Robot B"


def test_build_episode_data_is_json_serializable():
    entries = [entry("a", "move", "move", 2, 24, 100.0)]
    scenario = BY_ID["dying_battery_vs_fragile_cargo"]
    data = build_episode_data(scenario, "stubborn", "always_yield", GRID, entries, None)
    json.dumps(data)  # must not raise
    assert data["negotiation"] is None
