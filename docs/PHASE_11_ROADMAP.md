# Phase 11 — The "O": a continuous, multi-robot corridor world

*Roadmap. No file-level implementation plan yet — that comes per sub-phase,
starting with 11a once 10b-3 and the `--firestore` visualizer are closed. This
document will be folded into `PLAN.md` (replacing the "Phase 11+" section) and
`DECISIONS.md` (a new D43) when 11a work begins; kept standalone until then.*

---

## Context

**Why this exists.** The project has run on exactly two robots, one linear
corridor, and one negotiation per episode since Phase 3. `PLAN.md` §9 ("Three or
more robots") was always the placeholder for going wider, but it was scoped as a
`world.py` rewrite **plus** an N-way-negotiation design fork (2 robots decide one
binary; 3+ need an ordering with no coordinator) **plus** discovery/broadcast
infra (Firestore registry, Pub/Sub). That bundle never had a clean first step,
and it was deferred indefinitely.

**What changed.** Reframing the world as a **loop** ("O" shape) instead of a line
dissolves the hardest part. On a loop with **directional lanes** and **1-lane
pinch-point corridors**, every genuine conflict is still **two robots at one
corridor** — so the pairwise `negotiate()` engine, the `{intent, goes_first,
text}` message schema, the A2A transport, and `wire.py` all survive essentially
untouched. The N-way problem is handled by the *world* (many robots circulating,
queuing at corridor mouths), not by the negotiation protocol. Discovery collapses
too: the world already knows every position, so it can just tell an approaching
robot who is at the far mouth — no registry, no Pub/Sub.

The loop also makes the world **continuous**: robots circulate forever and
negotiate the same corridors repeatedly. That is the setting the Phase 11+
experiments (cost of communication, individual scoring, killing a robot
mid-negotiation, refusing to disclose, reputation over repeated encounters)
always assumed but never had.

**Decision:** the old **Phase 9 is superseded**. This becomes **Phase 11**. The
protocol experiments previously listed under "Phase 11+" become **Phase 12+** —
experiments run *on* this world.

**Intended outcome:** a small fleet (up to 8) of independently-running robots
circulating an O-shaped track, resolving each corridor crossing by pairwise
negotiation, with a survival-based score — first in one process, ending with N
Cloud Run services and a Firestore-backed loop world.

---

## The world model

### Geometry
- A **rectangular loop** — 4 corners, 4 sides. Positions are integers `0..L-1`
  around the loop (mod `L`).
- **Two lanes** on every side, **directional**: CW robots occupy one lane, CCW
  the other. Opposing traffic on a 2-lane side therefore never conflicts.
- **Two 1-lane corridors**, one on each of **two opposite sides**, each placed
  **off-centre** on its side and ideally of **different length**, so a robot's
  two crossings per lap are never mirror images (the D21 asymmetric idea, done
  as geometry rather than a tuned constant).
- A **1-lane corridor admits one robot at a time** (head-on can't pass;
  same-direction following in a corridor is disallowed in 11a — revisit later).

### Robots
- **Spawn slots:** each of the 4 corners × {CW, CCW} → **8 max**. Two robots may
  share a corner only if heading opposite ways (different lanes).
- A run config is a list of robots, each: `{corner, direction, urgency,
  situation}`.
- Robots **circulate forever** in their fixed direction. No target, no "reached
  target", no episode terminus.

### The life / urgency / death mechanic
- Every robot starts with **life = 100**.
- `urgency` is a **drain rate in life-points per tick, applied only while the
  robot is yielding / blocked** (stopped at a corridor mouth waiting its turn).
  A robot that is moving — including free-flow and its turn through a corridor —
  loses nothing.
- **Default drain 20/tick** (≈5 ticks of waiting kills a baseline robot);
  scenarios set higher for urgent robots, lower for patient ones.
- `urgency` is **fixed per robot for the whole run** (re-rolled only between
  runs, like a seed). Stable "character" is what later reputation work needs.
- Life reaches 0 → the robot is **removed from the world** (not left as an
  obstacle).
- **No replenishment** in Phase 11. A run is finite; survival is the metric.
- Consequence: a robot that never yields never loses life. The score therefore
  directly punishes losing negotiations and over-accommodating.

### The reactive invariant (generalises `reactive_filter`)
Re-derived every tick, independently of whatever the executive layer decided:
1. **At most one robot per `(lane, position)` cell.**
2. **At most one robot in each 1-lane corridor at a time.**
3. A robot may only *enter* a corridor if it is empty.

`reactive_filter(a_action, b_action) -> (str, str)` becomes a pass over the
robot list returning one action per robot.

### Negotiation — unchanged
- Trigger: a robot reaches a corridor's boundary cell **and** senses an opposing
  robot at/near the far mouth (today's "at boundary + sense other", generalised
  to "the contender for *this* corridor").
- The **front robot of each side** runs `negotiate()` exactly as today —
  pairwise, `goes_first ∈ {me, other}`, one PROPOSE + one ACCEPT, `max_turns`
  budget. `negotiation.py`, `Message`, the LLM tool schema, `agent_executor.py`'s
  1:1 ping-pong: all stay.
- **Queue resolution:** the winner passes through; then whoever is now at the
  front of each side **re-negotiates**. A robot that has been waiting has lower
  life, so its next contest is weighted toward it — no starvation, no batch-drain
  rule needed.

### Discovery — through the world
`get_observation` already sees all positions. It returns, for the corridor a
robot is approaching, the **contender at the far mouth: name + advertise-URL**.
That is the entire discovery mechanism. No Firestore registry, no Pub/Sub. This
revises `PLAN.md` §9's infra assumptions.

### The one design principle — still satisfied
Each robot privately holds its `urgency`, its remaining `life`, and its
`situation`; the world reveals only sensed positions and the far-mouth
contender's identity. A single prompt with every robot's urgency and life would
schedule the corridors optimally — so the information asymmetry is real, not
decoration.

---

## What stays vs what changes

### Stays (the payoff of the loop framing)
- `src/negotiation.py` — the whole engine, `Message`, `Intent`, `check_agreement`,
  all policies, both LLM tool schemas.
- `src/wire.py` — already addressing-agnostic, round-trips by value.
- A2A transport shape — one task = one initiator + one responder, per corridor
  contest.
- `src/tracing.py` — generic; one trace tree per pairwise negotiation.

### Changes
| File | Change |
|---|---|
| `src/world.py` | **Core rewrite.** `WorldState.a/.b` → `robots: dict[str, RobotState]`. Loop positions (mod `L`), `lane` field, two corridor spans, directional-lane model. `reactive_filter` / `apply` / `executive_decide` → list passes. `priority` (one name) → per-corridor priority. Life/urgency/death bookkeeping. Remove `run_episode`'s `reached_target` termination → run for a fixed tick budget. `RobotState` itself mostly survives (add `lane`, drop fixed `boundary`/`target` constants — derive from geometry). |
| `src/scenarios.py` | `Scenario` `a_*/b_*` → `robots: list[{situation, urgency}]` (urgency = drain rate). `should_go_first` → per-encounter "who is closer to death" is computed live, not stored. `for_side(id, "a"|"b")` → `for_robot(id, index_or_name)`. |
| `src/observation.py` | Single `other_*` → a list of sensed others (ahead / behind / at which corridor). Drop "approaching from the opposite end" phrasing (false on a loop). |
| `world_store.py` | `world/current` doc: `{a_position, b_position}` → `{robots: {name: {pos, lane, dir, life, ...}}, corridors: {...}}`. `state_from_positions(a, b)` → from the robots map. `_resolve` generalises "all others wait" (already its logic, currently a/b-branched). |
| `world_server.py` | `get_observation(side)` → keyed by robot name; returns list of sensed others + the far-mouth contender (name + URL). `propose_action` keyed by name, validates the name. `record_negotiation` → possibly several per run, keyed by (corridor, tick). |
| `agent.py` | Drop `--side a/b`; take `--name` / `--advertise-url` / `--world-url`. Remove `--peer-url` (learned from the world). `build_robots` → `me` + a *set* of possible others. `is_my_turn_to_initiate` → "smaller name in the contending pair" (already lexicographic). `priority_holder` / `dial_holder` / `incoming_active` → per-corridor dicts. |
| `simulate.py` | Continuous run over a tick budget; survival / throughput / death readout instead of the 6-tuple tick dump. |
| `world_eval.py` | `policy_a`/`policy_b` CSV columns → a robot-list config per row. `correct` → per-encounter "closer-to-death robot won" rate; add total-life-preserved, time-to-first-death, deaths, corridor throughput, fairness across robots. |
| `visualize*.py` | Loop layout; multiple negotiation markers per run; life bars. Later. |

---

## Sub-phases

Each leaves something runnable. Deterministic policies carry 11a–11b so the whole
thing is testable with no API cost before an LLM is involved.

### 11a — the loop, ≤2 robots, deterministic, in-process
Rewrite `world.py` to the loop model. Two robots, opposite directions,
circulating. Generalised `reactive_filter`. Continuous run (tick budget). Life /
urgency / death. Per-corridor priority. `simulate.py` adapted. `negotiation.py`
untouched.
**Done when:** zero collisions over a long run; both robots lap the track and
contest both corridors repeatedly; a robot forced to wait bleeds down and can
die; `simulate.py` prints a survival summary.

### 11b — N robots (3–8), deterministic, in-process
Corner/direction spawn config. Queues behind corridor mouths; "winner passes,
re-negotiate". Per-corridor pairwise contests. `scenarios.py` → per-robot list.
`world_eval.py` → survival / throughput / fairness aggregates over seeded runs.
**Done when:** an 8-robot run resolves cleanly with deterministic policies, no
collision, no starvation deadlock; the eval sweep produces stable numbers.

### 11c — LLM policies, continuous
`SYSTEM` prompt reframed: quantified stakes ("you lose N life/tick while stopped;
you have M left"), "this is one of many crossings", no "opposite ends" language.
`observation.py` loop-aware. Cross-provider (Claude / Gemini) as today.
**Done when:** LLM robots circulate and negotiate live; the closer-to-death robot
wins materially more often than chance; a policy visibly exploits a peer that
keeps yielding.

### 11d — networked, N processes
`get_observation` returns the far-mouth contender (name + URL) → discovery.
`agent.py` de-sided. Per-corridor dial/priority state. `world_store` /
`world_server` robots-map doc. Deploy as N Cloud Run services (generalise the
D41 three-service pattern); Firestore-backed loop world.
**Done when:** N deployed robot services circulate a deployed loop world, contest
corridors over real A2A + OIDC, one Cloud Trace per crossing; the visualizer
replays a fully deployed continuous run.

### 11e — memory / reputation (deferred, own design later)
Robots recognise repeat opponents; trust, retaliation, and exploitation over
repeated encounters. Needs a persistent per-robot store of past crossings and a
policy that reads it. Explicitly **out of scope** until 11a–11d are done — this
is where the "different objectives" pressure gets most interesting and deserves
its own planning pass.

---

## Deliberately deferred
- **Replenishment / charging cells** — 11a–11e run finite. Charging adds a
  routing dimension; revisit as a Phase 12 experiment.
- **Passing on 2-lane sections** — directional lanes only. Overtaking is a second
  negotiation type; a later variant.
- **Dead robot as obstacle** — removed, not left in place. (The blocking-death
  threat still exists implicitly: a death *in* a corridor during 11a's
  one-at-a-time rule can't happen mid-transit under "drains only while yielding",
  so this stays simple.)
- **Dynamic join/leave** — spawn is start-only through 11d. A robot joining a
  running world is what would finally justify a registry; pair it with 11e.
- **N-way (k>2) negotiation** — not needed. The loop keeps every contest 2-robot.
- **Pub/Sub, Firestore registry** — replaced by world-mediated discovery.
- **3D / real simulator / ROS** — unchanged from `PLAN.md` §7.

---

## Open items to settle at 11a implementation time
1. Concrete `L`, corner positions, and the two corridor spans (lengths +
   offsets).
2. Same-direction following inside a corridor — disallowed in 11a; is it ever
   allowed?
3. Tick budget / run length for a finite run, and the tick↔"second" mapping for
   the drain rate (`POLL_INTERVAL_SECONDS` is 1.0 today).
4. The urgency distribution / scenario deck for 3–8 robots.
5. Exactly when the negotiation trigger fires relative to the boundary cell on a
   loop (today it's "at boundary + sensed"); confirm it still fires early enough
   given corridor lengths differ.
6. Whether `world_eval` runs continue to be seeded-deterministic for the LLM
   phases or move to a fixed transcript replay.

---

## Doc changes this roadmap will drive (when 11a begins)
- **`PLAN.md`**: mark Phase 9 superseded; replace the "Phase 11+ — the actual
  project" section with this (11a–11e); renumber the protocol experiments to
  Phase 12+. Update §4 diagram (loop, N robots), §6 ("where does the simulator
  run" — now a deployed Service), §9 metrics (add survival / fairness).
- **`DECISIONS.md`**: new entry (**D43**) — "The O: Phase 9 replaced by Phase 11".
  Record: why the loop dissolves the N-way fork (directional lanes + 1-lane
  corridors localise every conflict to 2 robots); why discovery goes through the
  world, not a registry (kills the Pub/Sub dependency); the life/urgency/death
  mechanic and its "drain only while yielding" choice; what's deferred (memory,
  passing, replenishment, obstacle-deaths, dynamic join). "Would change our
  mind": if same-direction corridor contention or dynamic join turns out to
  matter early, the registry comes back.

---

## Verification (per sub-phase, when built)
- **11a/11b:** `python simulate.py` (loop config) runs a full tick budget with
  **zero collisions** (assert in `reactive_filter` tests), robots complete
  multiple laps, at least one death occurs under an adversarial config;
  `python world_eval.py` sweep produces stable survival/throughput numbers;
  full `pytest` green (the collision-impossibility tests are the load-bearing
  ones).
- **11c:** live `simulate.py` with `--policy llm` / `gemini`; inspect that the
  closer-to-death robot wins > chance across ~20 seeded encounters; check the
  `llm.respond` transcripts show the quantified-stakes reasoning.
- **11d:** 3-terminal then deployed — N robot processes + loop world; one Cloud
  Trace per crossing; `visualize_network.py` replays a continuous run;
  unauthenticated call to any robot / the world → 403.
