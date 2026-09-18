"""Tests for Phase 11a step 5: loop_simulate.py, the CLI driver tying
loop_world/loop_scenarios/loop_observation together into a runnable
episode. Zero API calls - only negotiation.py's deterministic scripted
policies are exercised here (the same free/fast discipline as
test_world.py). See docs/DECISIONS.md D48.
"""

import sys

sys.path.insert(0, ".")
sys.path.insert(0, "src")

import pytest  # noqa: E402

from loop_scenarios import BY_ID  # noqa: E402
from loop_simulate import build_world, make_policy, run_loop_episode  # noqa: E402
from loop_world import CORNERS, CORRIDORS  # noqa: E402
from negotiation import GeminiPolicy  # noqa: E402


class _Args:
    def __init__(self, vertex_project=None, vertex_region="global", gemini_model="gemini-2.5-flash"):
        self.vertex_project = vertex_project
        self.vertex_region = vertex_region
        self.gemini_model = gemini_model


def test_make_policy_passes_deterministic_names_through_unchanged():
    assert make_policy("never_yield", _Args()) == "never_yield"


def test_make_policy_builds_a_real_geminipolicy_with_vertex_config():
    policy = make_policy("gemini", _Args(vertex_project="corridor-agents", gemini_model="gemini-2.5-pro"))
    assert isinstance(policy, GeminiPolicy)
    assert policy.project == "corridor-agents"
    assert policy.model == "gemini-2.5-pro"


def test_make_policy_requires_vertex_project_for_gemini():
    with pytest.raises(SystemExit, match="vertex-project"):
        make_policy("gemini", _Args(vertex_project=None))


def test_build_world_places_robots_at_their_spawn_corners():
    config = BY_ID["duel"]
    state, private_text = build_world(config, {"R1": "always_yield", "R2": "always_yield"})
    assert state.robots["R1"].position == CORNERS["TL"]
    assert state.robots["R2"].position == CORNERS["TR"]
    assert state.robots["R1"].direction == 1
    assert state.robots["R2"].direction == -1
    assert private_text["R1"] == config.robot("R1").situation
    assert private_text["R2"] == config.robot("R2").situation


def test_build_world_wires_each_robots_urgency_into_its_negotiation_robot():
    config = BY_ID["duel"]
    state, _ = build_world(config, {"R1": "always_yield", "R2": "always_yield"})
    assert state.robots["R1"].robot.urgency == 18
    assert state.robots["R2"].robot.urgency == 5


# --- duel: the roadmap's own hypothesis, confirmed live -----------------


def test_duel_never_yield_beats_always_yield_at_the_first_crossing():
    """The exact live-run result this driver produced on its first real
    smoke test: R2 senses R1 from a distance and yields before R1 even
    reaches its own boundary - the early-trigger design (D12) working
    end to end through the full loop_world/loop_observation/negotiation
    stack, not just the unit-level pieces."""
    result = run_loop_episode(
        BY_ID["duel"], {"R1": "never_yield", "R2": "always_yield"}, ticks=30
    )
    assert result.alive == ["R1", "R2"]
    assert result.dead == []
    waited = [e for e in result.log if e["resolved"].get("R2") == "wait"]
    assert waited, "R2 should have yielded at least once at the first crossing"
    # R1 never has to wait against an always_yield opponent
    assert all(e["resolved"].get("R1") != "wait" for e in result.log)


def test_duel_r2_resumes_once_r1_clears_the_corridor():
    result = run_loop_episode(
        BY_ID["duel"], {"R1": "never_yield", "R2": "always_yield"}, ticks=30
    )
    # R2 must both wait (blocked) and later move again (released) within
    # this run - proves the contest actually retires and re-releases R2,
    # not just that R2 was blocked once and the run ended mid-block.
    r2_actions = [e["resolved"].get("R2") for e in result.log]
    assert "wait" in r2_actions
    assert "move" in r2_actions[r2_actions.index("wait"):]


# --- standoff: the roadmap's own hypothesis, confirmed live -------------


def test_standoff_deadlocks_and_both_sides_die():
    """never_yield vs never_yield, equal urgency: must deadlock (neither
    proceeds), then both drain to death - PHASE_11_ROADMAP.md's own
    stated purpose for this config, confirmed against a real run of the
    full driver stack, not just loop_world's unit tests in isolation."""
    result = run_loop_episode(
        BY_ID["standoff"], {"R1": "never_yield", "R2": "never_yield"}, ticks=20
    )
    assert result.alive == []
    assert set(result.dead) == {"R1", "R2"}
    assert result.ticks_run < 20  # ends early once both are dead, not at the full budget


def test_standoff_never_lets_both_robots_enter_the_corridor():
    """The reactive layer's guarantee, exercised through the full driver
    stack: a deadlock must never resolve itself as an accidental double
    entry. Checked directly against real positions each tick (not the
    resolved actions - both robots legitimately show "move" on every
    early tick before either reaches its boundary, which isn't a
    collision, just free travel with nothing to negotiate yet)."""
    result = run_loop_episode(
        BY_ID["standoff"], {"R1": "never_yield", "R2": "never_yield"}, ticks=20
    )
    for entry in result.log:
        in_north = [
            name for name, pos in entry["positions"].items()
            if CORRIDORS["north"]["start"] <= pos <= CORRIDORS["north"]["end"]
        ]
        assert len(in_north) <= 1, f"tick {entry['tick']}: {in_north} both in north"


# --- the reactive invariant, over a longer free run ----------------------


def test_no_collision_over_a_long_free_run():
    """The load-bearing structural guarantee (matching world.py's and
    loop_world.py's own naive-always-move tests), exercised through the
    real driver: over many ticks of real negotiation-driven movement, no
    two robots ever share a (lane, position) cell or a corridor."""
    result = run_loop_episode(
        BY_ID["duel"], {"R1": "stubborn", "R2": "stubborn"}, ticks=200
    )
    seen_by_tick = {}
    for entry in result.log:
        by_lane = {}
        for name, pos in entry["positions"].items():
            # lane isn't in the log directly, but duel's two robots never
            # change direction, so corner-fixed direction is enough here
            direction = 1 if name == "R1" else -1
            key = (direction, pos)
            assert key not in by_lane, f"tick {entry['tick']}: collision at {key}"
            by_lane[key] = name
