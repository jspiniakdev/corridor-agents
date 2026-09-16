"""Tests for Phase 11a step 3: loop_scenarios.py's spawn config format and
the duel/standoff starter configs. Zero API calls, no policies involved.
See docs/DECISIONS.md D48, docs/PHASE_11_ROADMAP.md's "Run and config".
"""

import sys

import pytest

sys.path.insert(0, "src")

from loop_scenarios import BY_ID, CONFIGS, LoopConfig, RobotSpawn, for_robot  # noqa: E402


def test_starter_configs_are_registered():
    assert set(BY_ID) == {"duel", "standoff"}
    assert BY_ID["duel"] is CONFIGS[0]
    assert BY_ID["standoff"] is CONFIGS[1]


def test_duel_matches_the_roadmap_spec():
    cfg = BY_ID["duel"]
    r1 = cfg.robot("R1")
    r2 = cfg.robot("R2")
    assert (r1.corner, r1.direction, r1.urgency) == ("TL", 1, 18)
    assert (r2.corner, r2.direction, r2.urgency) == ("TR", -1, 5)


def test_standoff_is_symmetric_urgency():
    cfg = BY_ID["standoff"]
    r1 = cfg.robot("R1")
    r2 = cfg.robot("R2")
    assert r1.urgency == r2.urgency == 20
    assert r1.corner == "TL" and r1.direction == 1
    assert r2.corner == "TR" and r2.direction == -1


def test_robot_raises_for_an_unknown_name():
    with pytest.raises(KeyError):
        BY_ID["duel"].robot("R99")


def test_for_robot_returns_only_that_robots_situation_and_urgency():
    situation, urgency = for_robot("duel", "R1")
    spawn = BY_ID["duel"].robot("R1")
    assert situation == spawn.situation
    assert urgency == spawn.urgency


def test_urgency_and_situation_stay_out_of_each_others_slice():
    """The hidden-information discipline (D16, generalized): asking for
    R1's slice must never leak R2's numbers."""
    s1, u1 = for_robot("duel", "R1")
    s2, u2 = for_robot("duel", "R2")
    assert u1 != u2
    assert s1 != s2


# --- LoopConfig validation --------------------------------------------


def _spawn(name="R", corner="TL", direction=1, urgency=10):
    return RobotSpawn(name=name, corner=corner, direction=direction, urgency=urgency, situation="x")


def test_rejects_more_than_eight_robots():
    # 9 robots, all crammed into the same slot - the count check must fire
    # before the slot-uniqueness check even gets a chance to.
    too_many = tuple(_spawn(name=f"R{i}") for i in range(9))
    with pytest.raises(ValueError, match="at most 8"):
        LoopConfig(id="too_many", robots=too_many)


def test_rejects_an_empty_robot_list():
    with pytest.raises(ValueError, match="at least one robot"):
        LoopConfig(id="empty", robots=())


def test_rejects_an_unknown_corner():
    with pytest.raises(ValueError, match="unknown corner"):
        LoopConfig(id="bad_corner", robots=(_spawn(corner="XX"),))


def test_rejects_an_invalid_direction():
    with pytest.raises(ValueError, match="direction must be"):
        LoopConfig(id="bad_direction", robots=(_spawn(direction=0),))


def test_rejects_a_reused_corner_direction_slot():
    robots = (_spawn(name="R1", corner="TL", direction=1), _spawn(name="R2", corner="TL", direction=1))
    with pytest.raises(ValueError, match="spawn slot"):
        LoopConfig(id="dup_slot", robots=robots)


def test_allows_the_same_corner_with_opposite_directions():
    """Two robots CAN share a corner, as long as they head opposite ways -
    different lanes, no conflict (PHASE_11_ROADMAP.md's spawn-slot rule)."""
    robots = (_spawn(name="R1", corner="TL", direction=1), _spawn(name="R2", corner="TL", direction=-1))
    cfg = LoopConfig(id="shared_corner", robots=robots)
    assert len(cfg.robots) == 2


def test_rejects_a_reused_robot_name():
    robots = (_spawn(name="R1", corner="TL", direction=1), _spawn(name="R1", corner="TR", direction=-1))
    with pytest.raises(ValueError, match="used more than once"):
        LoopConfig(id="dup_name", robots=robots)


def test_all_eight_slots_fit_in_one_config():
    robots = tuple(
        _spawn(name=f"R{i}", corner=corner, direction=direction)
        for i, (corner, direction) in enumerate(
            (c, d) for c in ["TL", "TR", "BR", "BL"] for d in (1, -1)
        )
    )
    cfg = LoopConfig(id="full_house", robots=robots)
    assert len(cfg.robots) == 8
