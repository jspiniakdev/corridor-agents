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

**Status: Phases 1–7 complete; Phase 8 partial; tracing through Phase 10b, with
the deployed world Service (10b-3b-ii) still to wire up.** Two robots negotiate
over a real 1D grid, over real A2A (peer to peer, no coordinator), moving through
a world that is its own MCP server — with a structural guarantee they can never
collide. The fleet runs containerized under Docker Compose, and both robots
deploy to Cloud Run with per-agent service accounts and OIDC agent-to-agent auth.
The negotiation policy runs against either Claude or Gemini. The whole path is
traceable to Cloud Trace, and world state can live in Firestore. Phase 9 (3+
robots) is deliberately deferred — staying at 2. See `docs/PLAN.md` for the full
arc.

## Run it

Everything below assumes the venv is active (`source .venv/bin/activate`,
Python 3.13). Tests need no API key and run in ~1s:

```bash
pip install -r requirements.txt
python -m pytest tests/ -q        # 126 tests, zero API calls
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

### Deployed (Phase 8)

`robot-b` runs as a Cloud Run Service (locked down, own service account),
`robot-a` as a Cloud Run Job (one-shot: dial, negotiate, exit). The full
`gcloud` commands and the one-time IAM setup are in `CLAUDE.md` and `docs/DECISIONS.md`
(D33). World state can move to Firestore with `world_server.py --firestore`;
`visualize_network.py --firestore` replays a Cloud Run episode with nothing local.

## Layout

```
docs/PLAN.md                 the project, the phases, the infra reasoning — source of truth
docs/DECISIONS.md            why things were chosen (D1–D40)
docs/SETUP.md                the working environment and gotchas already hit

src/negotiation.py           messages, policies (incl. LLMPolicy / GeminiPolicy), the exchange loop
src/scenarios.py             corridor scenarios with hidden ground-truth urgency; for_side() slicing
src/observation.py           computes what a robot can see from position + sensors
src/world.py                 the grid, the reactive/executive layers, the tick loop
src/wire.py                  A2A message (de)serialization
src/tracing.py               stdlib-only OpenTelemetry shim, no-op until --trace

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

### Phase 8 — deploy *(partial)*

- **`robot-b` is a Cloud Run Service, `robot-a` a Cloud Run Job.** `--side a`
  without `--world-url` is a genuine one-shot process (dial, negotiate, exit);
  Jobs are exactly right for that and exactly wrong for what Services expect.
- **Agent identity is now a security boundary.** Each robot has its own service
  account; they authenticate to each other with real OIDC ID tokens (`--auth`).
  Verified live both ways, and an unauthenticated `curl` to `robot-b` confirmed
  `403`. See D33.
- **Claude on Vertex (D34, `--vertex`)** — authenticates as the calling process's
  GCP identity, no API key. Built and unit-tested but **not yet live-verified**:
  the fresh project's `anthropic-claude-sonnet` quota is 0 (support ticket open).
- **`GeminiPolicy` (D36, `--policy gemini`)** — Claude's exact negotiation
  contract, reimplemented against Gemini via Vertex. Verified live. Since
  Gemini's Vertex quota *is* non-zero, it's how the deployed pipeline gets
  exercised while the Claude quota is stuck.
- **Secret Manager was dropped, deliberately (D34).** On the Vertex path there's
  no API key left in the cloud for it to protect.

### Phase 10 — see what's happening

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
  Verified live 3-terminal: two `--serve` robots ran 3 episodes without
  restarting.

## Next

- **A live Vertex smoke test** — blocked on the GCP quota increase (auto-denied
  on the fresh project, support ticket open). Then redeploy `robot-a`/`robot-b`
  with `--vertex` + `--trace`.
- **Phase 10b-3b-ii** (all `gcloud`) — a `world@` service account, IAM bindings
  (`roles/datastore.user`, `roles/cloudtrace.agent`, `roles/run.invoker` for the
  robot SAs), deploy `world_server.py` as a Service (`--firestore --trace`),
  convert `robot-a` Job→Service, redeploy both with `--world-url --serve`, and
  verify the 3-service trace tree. See `docs/PLAN.md` §5.

**Phase 9 (three or more robots) is deliberately skipped** — a `world.py` rewrite
plus an N-way-negotiation design fork, and the discovery/broadcast half only
earns its keep at 3+ robots. Staying at 2.
