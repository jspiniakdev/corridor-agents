#!/usr/bin/env python3
"""Phase 11a step 5: run one loop-world episode and print it, tick by tick.

    python loop_simulate.py                            # duel, stubborn vs stubborn
    python loop_simulate.py --config standoff
    python loop_simulate.py --a llm --b llm             # needs an API key
    python loop_simulate.py --a gemini --b gemini --ticks 280   # the roadmap's "real run" length

Deterministic policies need no API key; see simulate.py's docstring for
what the LLM policies need. Unlike simulate.py's episode (which ends once
both robots reach a target), a loop episode has no terminus - PLAN.md's
"no target on a loop" - so this always runs for exactly `--ticks` ticks
(default 40, enough to see at least one full crossing at each corridor;
the roadmap's own suggested "real run" is 280 - ~6 laps, ~12 crossings
per robot), or until every robot has died, whichever comes first.

`--config duel`/`--config standoff` are the only starter configs built so
far (loop_scenarios.py); both are exactly 2 robots, so --a/--b name their
policies the same way simulate.py's --a/--b do. `crowd` (11b, N robots)
isn't built yet.
"""

import argparse
import sys

sys.path.insert(0, "src")

from dataclasses import dataclass, field  # noqa: E402

from loop_observation import compose_loop_observation  # noqa: E402
from loop_scenarios import BY_ID, LoopConfig  # noqa: E402
from loop_world import CORNERS, SENSOR_RANGE, RobotState, WorldState, step, survival_result  # noqa: E402
from negotiation import POLICIES, Robot  # noqa: E402


def build_world(config: LoopConfig, policy_names: dict[str, str]) -> tuple[WorldState, dict[str, str]]:
    """Build a fresh WorldState from a LoopConfig, one negotiation.Robot
    per spawn, at full life. Returns (state, private_text) - private_text
    is each robot's own hand-authored situation, captured once so it never
    compounds as compose_loop_observation overwrites .situation every tick
    with live position/sensor facts (same pattern as agent.py's
    run_robot, D24)."""
    robots = {}
    private_text = {}
    for spawn in config.robots:
        policy = POLICIES[policy_names[spawn.name]]()
        nego = Robot(name=spawn.name, situation=spawn.situation, urgency=int(spawn.urgency), policy=policy)
        robots[spawn.name] = RobotState(
            name=spawn.name,
            position=CORNERS[spawn.corner],
            direction=spawn.direction,
            corner=spawn.corner,
            robot=nego,
            life=100.0,
            urgency=spawn.urgency,
        )
        private_text[spawn.name] = spawn.situation
    return WorldState(robots), private_text


@dataclass
class EpisodeResult:
    log: list = field(default_factory=list)   # one entry per tick: {tick, positions, resolved, dead}
    survival: dict = field(default_factory=dict)  # name -> ticks survived, or the tick it died
    ticks_run: int = 0
    alive: list = field(default_factory=list)
    dead: list = field(default_factory=list)


def run_loop_episode(
    config: LoopConfig,
    policy_names: dict[str, str],
    ticks: int,
    max_negotiation_turns: int = 6,
    sensor_range: int = SENSOR_RANGE,
) -> EpisodeResult:
    """Run a loop episode for exactly `ticks` ticks - no terminus, robots
    circulate for as long as they're alive. Recomposes every surviving
    robot's observation fresh each tick before stepping (D24's discipline,
    generalized) - a once-computed, never-refreshed observation would let
    a robot miss a contender it should by now be able to sense."""
    state, private_text = build_world(config, policy_names)
    log = []
    for _ in range(ticks):
        if not state.robots:
            break  # everyone's dead - nothing left to simulate
        for name in list(state.robots):
            robot = state.robots[name]
            robot.robot.situation = compose_loop_observation(state, name, sensor_range, private_text[name])
        result = step(state, max_negotiation_turns)
        log.append({
            "tick": state.tick,
            "positions": {name: r.position for name, r in state.robots.items()},
            "resolved": result["resolved"],
            "dead": result["dead"],
        })

    survival = survival_result(state)
    alive = list(state.robots)
    dead = [name for name in survival if name not in state.robots]
    return EpisodeResult(log=log, survival=survival, ticks_run=state.tick, alive=alive, dead=dead)


def format_config_header(config: LoopConfig, policy_names: dict[str, str]) -> str:
    lines = [f"config: {config.id}"]
    for spawn in config.robots:
        direction_label = "CW" if spawn.direction == 1 else "CCW"
        lines.append(
            f"  {spawn.name} ({policy_names[spawn.name]}): {spawn.corner}/{direction_label}, "
            f"urgency {spawn.urgency} - {spawn.situation}"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default="duel", choices=list(BY_ID))
    parser.add_argument("--a", default="stubborn", choices=list(POLICIES), help="first robot's policy")
    parser.add_argument("--b", default="stubborn", choices=list(POLICIES), help="second robot's policy")
    parser.add_argument("--ticks", type=int, default=40)
    parser.add_argument("--max-negotiation-turns", type=int, default=6)
    parser.add_argument("--summary-only", action="store_true", help="skip the per-tick log")
    args = parser.parse_args()

    config = BY_ID[args.config]
    if len(config.robots) != 2:
        parser.error(f"--a/--b assume exactly 2 robots; {args.config!r} has {len(config.robots)}")
    policy_names = {config.robots[0].name: args.a, config.robots[1].name: args.b}

    print(format_config_header(config, policy_names))
    print("-" * 72)

    result = run_loop_episode(config, policy_names, ticks=args.ticks, max_negotiation_turns=args.max_negotiation_turns)

    if not args.summary_only:
        for entry in result.log:
            pos_text = ", ".join(f"{name}@{pos}" for name, pos in entry["positions"].items())
            action_text = ", ".join(f"{name}:{action}" for name, action in entry["resolved"].items())
            dead_text = f"  DIED: {', '.join(entry['dead'])}" if entry["dead"] else ""
            print(f"  tick {entry['tick']:>4}: {pos_text}  [{action_text}]{dead_text}")

    print("-" * 72)
    print(f"ran {result.ticks_run} tick{'s' if result.ticks_run != 1 else ''}")
    for name in sorted(result.survival, key=lambda n: -result.survival[n]):
        status = "alive" if name in result.alive else f"died at tick {result.survival[name]}"
        print(f"  {name}: {status}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
