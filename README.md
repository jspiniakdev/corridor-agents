# Corridor Agents

Simulated robots that negotiate with each other, using LLMs, to share physical
space — deciding who goes first from each robot's own private sense of urgency
and criticality. Each robot knows something the others don't, and each is a
separate networked service.

A learning project, built one layer at a time, to experiment with:

1. LLM policy — what an agent should say, commit to, and be scored on
2. Agent design — an identity + an interface + a pluggable policy
3. Agent-to-agent protocols — A2A, peer to peer, no central coordinator
4. MCP design — the world as a tool the robots query
5. Cloud infra for agents — one concrete pain at a time, no cargo-culting
6. Negotiation for robotics — mutual exclusion over shared space, and the
   deadlocks that come from getting it wrong

**Status: Phases 1–10 done, Phase 11a done. Phase 9 was superseded by Phase
11 — a loop-shaped, multi-robot world ("the O"), replacing the old "three or
more robots" plan entirely; 11a's 2-robot loop world is built and
live-verified against real Gemini, 11b (N robots) is next.** Two robots
negotiate over a real 1D grid, over
real A2A (peer to peer, no coordinator), moving through a world that is its own
MCP server — with a structural guarantee they can never collide. The fleet runs
containerized under Docker Compose, and deploys to Cloud Run as three real
Services (`world`, `robot-a`, `robot-b`) with per-agent service accounts, OIDC
agent-to-agent auth, and a Firestore-backed world reachable by nothing local.
The negotiation policy runs against Gemini via Vertex (Claude-on-Vertex's quota
request was declined outright — not a temporary block, a closed path). The
whole pipeline is traceable end to end in Cloud Trace, and the network replay
visualizer works against a live deployed episode. See `docs/PLAN.md` for the
full arc, `docs/PHASE_11_ROADMAP.md` for what's being built now.

## Run it

Everything below assumes the venv is active (`source .venv/bin/activate`,
Python 3.13). Tests need no API key and run in ~1s:

```bash
pip install -r requirements.txt
python -m pytest tests/ -q        # 186 tests, zero API calls
```

### In-process (Phases 1–3): one process, no network

```bash
python run.py                                  # one negotiation, deterministic, free, instant
python run.py --a never_yield --b never_yield  # watch them deadlock
python run.py --a llm --b llm                  # needs: cp .env.example .env && source .env

python eval.py                                 # measurement sweep, deterministic cases only
python eval.py --full                          # also the llm-involving cases - costs money

python simulate.py                             # one grid episode, deliberate, deterministic
python simulate.py --no-deliberate --a llm --b llm   # FCFS baseline vs. negotiation

python world_eval.py                           # grid-episode sweep, free cases only
python world_eval.py --full                    # also the deliberate llm-involving cases

python visualize.py                            # tick-by-tick HTML replay of one Phase 3 episode
```

### Networked (Phases 5–6): real A2A between robot processes

Phase 5 — negotiation only, two terminals, same `--scenario` in each:

```bash
python agent.py --scenario <id> --side b --policy always_yield --port 9001
python agent.py --scenario <id> --side a --policy stubborn --peer-url http://127.0.0.1:9001
```

Phase 6 — three terminals: the world, then both robots moving through it.
Every robot can dial and be dialed; `--side a` starts first by convention.

```bash
python world_server.py --port 9500
python agent.py --scenario <id> --side a --policy stubborn     --port 9001 --peer-url http://127.0.0.1:9002 --world-url http://127.0.0.1:9500/mcp
python agent.py --scenario <id> --side b --policy always_yield --port 9002 --peer-url http://127.0.0.1:9001 --world-url http://127.0.0.1:9500/mcp

# after the episode, with world_server.py still running:
python visualize_network.py --scenario <id> --a stubborn --b always_yield --world-url http://127.0.0.1:9500/mcp
python export_timeline_csv.py --world-url http://127.0.0.1:9500/mcp   # raw, unfiltered CSV
```

Add `--serve` to both agents (D40, the Cloud Run Service shape) and they idle at
their target instead of exiting; start a fresh episode any time with
`python trigger_episode.py --world-url http://127.0.0.1:9500/mcp`. Against a
locked-down deployed world, add `--auth` to both agents and `trigger_episode.py`.

`agent.py`'s `--scenario`/`--policy` default to `routine_vs_medical`/`llm` — a
bare run makes real Anthropic API calls and needs `.env` sourced. Pass
`--policy stubborn` for a free, deterministic smoke test.

Extra flags: `--webhook` (initiator registers a callback instead of holding the
connection open), `--trace` (OpenTelemetry spans, both sides), `--policy gemini`
with `--vertex-project` (run the negotiation contract against Gemini via Vertex),
`--vertex` (Claude via Vertex instead of the direct API).

### Containerized (Phase 7)

```bash
source .env                       # ANTHROPIC_API_KEY, for the default --policy llm
docker compose up --build         # world + robot-a + robot-b, one command
```

The `world` container stays up after the robots exit; the local debug tools work
against it unchanged via the mounted `./experiments` volume.

### Deployed (Phase 8, 10b): three real Cloud Run Services

`world`, `robot-a`, and `robot-b` each deploy as their own Service, own
service account, Firestore-backed world state, OIDC agent-to-agent auth,
`--trace` wired to Cloud Trace. `robot-a` started as a one-shot Cloud Run Job
(D33) and was converted to a Service (D41) once `--serve` (D40) let robots idle
between episodes instead of exiting. The full `gcloud` commands and one-time
IAM setup are in `CLAUDE.md` and `docs/DECISIONS.md` (D33, D41).
`visualize_network.py --firestore` replays a live deployed episode with
nothing local running. **Cost note:** `robot-a`/`robot-b` need
`--min-instances=1` for `--serve`'s background loop, which bills ~$15–40/month
combined even idle — delete them between sessions (`world` scales to zero for
free; leave it).

## Layout

```
docs/PLAN.md                 the project, the phases, the infra reasoning — source of truth
docs/DECISIONS.md            why things were chosen (D1–D48)
docs/SETUP.md                the working environment and gotchas already hit
docs/PHASE_11_ROADMAP.md     the loop-world design (Phase 11, in progress) - geometry,
                              lanes, life/urgency/death, sub-phase breakdown

src/negotiation.py           messages, policies (incl. LLMPolicy / GeminiPolicy), the exchange loop
src/scenarios.py             corridor scenarios with hidden ground-truth urgency; for_side() slicing
src/observation.py           computes what a robot can see from position + sensors
src/world.py                 the linear grid, reactive/executive layers, the tick loop (Phases 1-10)
src/loop_world.py            NEW (Phase 11a, in progress): the loop world - geometry, directional
                              lanes, two corridors, generalized reactive_filter, negotiation trigger,
                              life/urgency/death. Lives alongside world.py, not in place of it - see
                              D48's file-layout note. No "winner": survival_result() reports ticks
                              survived per robot, nothing declares anyone a winner (D48 addendum).
src/wire.py                   A2A message (de)serialization
src/tracing.py                stdlib-only OpenTelemetry shim, no-op until --trace

run.py / eval.py             one negotiation / the measurement sweep (Phase 2)
simulate.py / world_eval.py  one grid episode / the grid-episode sweep (Phase 3)
visualize.py                 tick-by-tick HTML replay of a single-process episode
agent.py                     a robot as a networked process: A2A server + mover + dialer
agent_executor.py            NegotiationExecutor — the A2A task handler on the responder
world_server.py              the world as an MCP server (get_map/get_observation/propose_action/...)
world_store.py               InMemoryWorldStore (default) | FirestoreWorldStore (--firestore)
trigger_episode.py           the "start an episode" button — reset_world() over MCP (D40)
visualize_network.py         post-hoc HTML replay of a networked episode (--firestore aware)
export_timeline_csv.py       raw merged event timeline CSV, nothing collapsed
visualize_template.html      replay page HTML/CSS/JS, shared by visualize.py and visualize_network.py
Dockerfile / docker-compose.yml   Phase 7 — the fleet in containers

experiments/cases.csv        which scenario x policy pairings to run (Phase 2)
experiments/world_cases.csv  which scenario/policy/deliberate-mode combos to run (Phase 3)
experiments/results/         eval/visualizer output, gitignored
tests/                       deterministic tests, zero API calls
```

## What each phase established

### Phase 1 — two agents, one conversation

- **Policy is pluggable** (D7). `AlwaysYield`, `NeverYield`, `Stubborn`,
  `LLMPolicy` and `GeminiPolicy` all implement one interface, so baselines and
  LLM agents run on identical machinery and are directly comparable.
- **Messages are structured** — `{intent, goes_first, text}`. The decision lives
  in `goes_first` so rule-based policies can participate; `text` is commentary.
  FIPA-ACL's performative idea, rediscovered.
- **Ground truth is hidden.** Each scenario carries an `urgency` score no policy
  ever sees. A test enforces this.

### Phase 2 — make it measurable

- **What to test is data, not code.** `experiments/cases.csv` lists every
  (scenario, policy_a, policy_b, repeats) combination; `eval.py` just runs the
  file.
- **"Correct" is `None`, not `False`, when no decision was made.** A tie and a
  deadlock both leave `correct` blank rather than counted as wrong.
- **The first number:** agreement rate, correctness rate (over decided episodes),
  and average messages, across deterministic episodes.

### Phase 3 — add the world

- **Collision is structurally impossible, not just avoided.** `reactive_filter`
  in `src/world.py` independently re-derives whether two proposed moves would put
  both robots in the corridor zone at once — a bug upstream can cause starvation,
  never a collision.
- **Negotiation is the exception, not the rule.** Whichever robot reaches its
  boundary first usually just claims priority — zero LLM calls. The deliberative
  layer fires only on a genuine standoff: a robot at its boundary that can
  currently *sense* the other, even before it arrives.
- **The observation is computed, not fixed.** `src/observation.py` builds a
  robot's situation fresh at the standoff, from real position and sensor facts
  plus the hand-authored private text.
- **The first grid number:** completion rate, negotiation rate, correctness, and
  average ticks used, across free episodes (deliberate deterministic pairings +
  FCFS baselines).
- **A visualizer, ahead of schedule (D13).** `visualize.py` writes one
  self-contained HTML file per episode — positions tick by tick, pausing to
  reveal the negotiation dialogue.

### Phase 4 — split into separate processes *(superseded by Phase 5, kept for history)*

- Each robot ran as its own process, negotiating over a deliberately dumb
  homemade message board. Enough to feel serialization, slow peers, and a peer
  that is simply *down* — questions with no meaning inside one process. See D14.

### Phase 5 — adopt A2A

- **Real A2A, peer to peer.** No third process at all. One negotiation is one A2A
  task; `negotiation.py` is completely unchanged — only the transport around it.
- Three pieces built: plain request/response (agent cards + task lifecycle,
  verified against `run.py`'s baseline exactly), streaming (a `WORKING`
  heartbeat), and webhooks (`--webhook`: the initiator stops holding the
  connection open). See D15.
- **Scenario isolation (D16).** A networked robot process never holds the full
  `Scenario` — only `scenarios.for_side()`'s own-half slice.

### Phase 6 — the world as an MCP server

- **`world_server.py` is a real MCP server** — `get_map()`,
  `get_observation(side)`, `propose_action(side, action)`, `get_log()`.
  `world.py`/`simulate.py` are untouched; the server reuses their
  `WorldState`/`reactive_filter`/`apply` directly.
- **The world's authority narrows** to exactly the one thing no single robot can
  safely decide alone: is it safe to enter the shared corridor zone right now.
- **Dynamic initiation (D18, D27, D30).** Every `agent.py` process runs its own
  A2A server *and* its own movement loop *and* can dial the peer the instant its
  observation says it's at the boundary and can sense the other. A genuine race
  is resolved by a fixed name tiebreak plus outbound-dial cancellation.
- **The network visualizer (D19–D28, D39)** and a **raw CSV timeline export
  (D29)** — post-hoc tools that read the episode log (and, on `--firestore`, the
  transcript) rather than inferring timing from movement.

### Phase 7 — containers

- One shared `Dockerfile`, one `docker-compose.yml` with three services on
  Compose's default network. `docker compose up` runs the exact same episode the
  three-terminal venv command does.
- **Two real bugs found only by running containers together:** both servers
  needed `--host 0.0.0.0` to bind reachably; and `a2a-sdk`'s `create_client()`
  builds its connection from the peer's *self-reported* AgentCard URL, not the
  string passed to it — fatal across containers. Fixed with `--advertise-url`.
  See D32.

### Phase 8 — deploy

- **`robot-b` and (from D41) `robot-a` both run as Cloud Run Services.**
  `robot-a` started as a Job (`--side a` without `--world-url` was a genuine
  one-shot: dial, negotiate, exit) and was converted once `--serve` (D40) gave
  robots an idle loop between episodes — a Job can't do that, a Service can.
- **Agent identity is a real security boundary.** Each robot has its own
  service account; they authenticate to each other with real OIDC ID tokens
  (`--auth`). Verified live, and an unauthenticated `curl` to `robot-b`
  confirmed `403`. See D33.
- **Claude on Vertex (D34, `--vertex`) is built, unit-tested, and permanently
  dead on this project** — the `anthropic-claude-sonnet` Vertex quota increase
  was declined outright, not just auto-denied pending review. No further
  action planned; the flag stays in the code (harmless) but won't work here.
- **`GeminiPolicy` (D36, `--policy gemini`)** is the permanent substitute, not
  a stopgap — Claude's exact negotiation contract, reimplemented against
  Gemini via Vertex, Gemini's Vertex quota being the one that's actually
  non-zero. The deployed robots run `gemini-2.5-pro` (D42-era note: pro
  reasons about the actual stakes instead of grasping at a bogus proximity
  claim, and resolves in ~2 messages vs flash's ~5).
- **Secret Manager was dropped, deliberately (D34).** On the Vertex path
  there's no API key left in the cloud for it to protect.

### Phase 10 — see what's happening. **Closed (D46).**

- **10a (D35):** `agent.py --trace` emits `negotiation.episode` →
  `negotiation.turn` → `llm.respond` on the initiator, `negotiation.handle` on
  the responder, plus a2a-sdk's own task-lifecycle spans. `src/tracing.py` is a
  stdlib no-op until `--trace`, so `run.py`/`eval.py`/tests pay nothing.
  Cross-process linking verified. Exporter from `OTEL_TRACES_EXPORTER` (`console`
  default, `gcp` for Cloud Trace).
- **10b-1 (D37):** MCP world-path tracing — `world.get_observation` /
  `world.propose_action` spans nest into the calling robot's trace.
- **10b-2 (D38):** Firestore-backed world behind a store seam.
  `InMemoryWorldStore` stays the default; `--firestore` swaps in a
  `@firestore.transactional` read-modify-write so two robots on two Cloud Run
  instances can't both enter a stale corridor. Verified live.
- **10b-3a:** the deployed negotiation, on Gemini, traced end-to-end to Cloud
  Trace — one trace spanning both Cloud Run services.
- **10b-3b code (D40):** `agent.py --serve` keeps `run_robot` alive past
  `reached_target` (a Service can't just exit) — it idle-polls until
  `reset_world()` starts a fresh episode and clears per-episode state. `--auth`
  now also mints an OIDC token for the MCP world channel, so a locked-down
  deployed world Service is reachable. `trigger_episode.py` is the start button.
- **10b-3b-ii (D41):** the full three-Cloud-Run-Service deployment —
  `world` (`--firestore --trace`, scale-to-zero) plus `robot-a`/`robot-b`
  (`--world-url --serve --auth --trace`, `--min-instances=1`). Verified live:
  a clean episode, and **one ~307-span Cloud Trace across all three Services.**
- **Four real deployed-path bugs, found and fixed (D42–D45):** an unrefreshed
  OIDC token crash-looping the robots hourly; `propose_action` not being
  idempotent (a retried MCP call double-moved a robot); `record_negotiation`
  with no episode guard, letting a late write from a dead episode clobber
  live state; and an LLM claiming a false proximity advantage because the
  observation didn't give it the other robot's actual distance to the
  corridor. Each verified fixed against the real deployment, not just locally.
- **D46:** the `--firestore` visualizer render, which looked broken, turned
  out to need zero code change once D42–D45 landed — verified with a headless
  jsdom frame-stepper (no browser in this environment) stepping through every
  frame of a real deployed episode. **Phase 10 closed.**
- **D47 (later, a grid tuning pass):** the linear grid lengthened (`MAX_POSITION`
  8→14) and the corridor moved right of center, with a real bug caught and
  fixed along the way — the first attempt at the shift silently skipped
  negotiation on the flagship demo scenario (an approach-room asymmetry
  outrunning `SENSOR_RANGE`), confirmed by simulation before shipping.

## Phase 9 → Phase 11: "the O"

**Phase 9 ("three or more robots") is superseded, not merely un-deferred
(D48).** The original plan needed a `world.py` rewrite plus a real
N-way-negotiation design fork plus discovery/broadcast infra (Firestore
registry, Pub/Sub) — that bundle never had a clean first step. Reframing the
world as a **loop** with directional lanes and two 1-lane pinch-point
corridors dissolves the fork entirely: every genuine conflict is still
exactly two robots at one corridor, so the pairwise `negotiate()` engine
survives untouched and the world absorbs the N-robot part. Discovery drops
the registry too — the world already knows every position, so it just tells
an approaching robot who's at the far mouth. Full design:
`docs/PHASE_11_ROADMAP.md`; rationale: D48.

### Phase 11a — loop world. **Done.**

Built alongside `world.py`, not replacing it (D48's file-layout note —
`world_server.py`/`world_store.py`/`agent.py` and the deployed pipeline all
import the linear grid directly, and none of that changes until 11c).

- **`src/loop_world.py`** — loop coordinates (`L=44`, two corridors),
  directional lanes, `reactive_filter`/`apply` generalized from two named
  robots to an arbitrary set (same discipline as D12: independently
  re-derived every tick). Sensing, the negotiation trigger, per-corridor
  `CorridorContest` (retires once both sides pass, so the same corridor
  negotiates fresh next time), and life/urgency/death. **No "winner"
  anywhere** (D48 addendum) — going first through a corridor isn't an
  outcome, just whose turn it is; the only real result is survival
  (`survival_result()` reports ticks-alive per robot).
- **`src/loop_scenarios.py`** — `duel` (18/5 urgency) and `standoff` (20/20)
  starter configs, self-validating `LoopConfig`/`RobotSpawn` dataclasses.
- **`src/loop_observation.py`** — loop-aware observation composition (a list
  of sensed others: ahead/behind/opposing, not one fixed `other_*` slot).
  Reuses `negotiation.py`'s `SYSTEM` prompt completely unchanged — "you're
  approaching a corridor from opposite ends" is still literally true on a
  loop.
- **`loop_simulate.py`** — the CLI driver, `--policy gemini` wired the same
  way `agent.py` does (`GeminiPolicy` needs Vertex config a bare
  `POLICIES[name]()` lookup can't carry).

**Live-verified against real `gemini-2.5-pro` via Vertex**, not just the
deterministic-policy tests: `duel` shows genuine urgency-aware reasoning
from prose alone — urgency itself never reaches the policy — with R1
("Coolant level critical... Requesting priority passage") correctly
claiming priority and R2 ("Acknowledged. You may proceed.") yielding and
resuming once R1 cleared; `standoff` produced a real 6-turn deadlock (both
sides genuinely held "hold firm," not a foregone conclusion) and both
robots drained to death exactly on schedule; a 70-tick `duel` run
circulated past both corridors multiple times, confirming the
retire-then-fresh-claim contest lifecycle live under real LLM timing, not
just in unit tests. See `docs/DECISIONS.md` D48 for the full record and
`docs/PHASE_11_ROADMAP.md` for the corridor-geometry bug found and fixed
during design review, before any code was written.

## Next

Phase 11b: N robots (3–8), corner/direction spawn config, queuing (the
`crowd` starter config), `world_eval.py`-equivalent survival/throughput/
fairness aggregates. See `docs/PHASE_11_ROADMAP.md`'s sub-phase list.
