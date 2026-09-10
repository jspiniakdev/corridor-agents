# Corridor Agents — Project Plan

*A learning project in multi-agent robotics coordination.*
*Last updated: 2026-09-09 · Status: Phases 1–7 done; Phase 8 partial (Vertex
flag-gated, quota-blocked); Phase 9 deferred (staying at 2 robots); Phase 10
split into 10a (done) + 10b-1/2/3. See `docs/DECISIONS.md` D1–D36 for the
real record; this file is the north-star plan, kept roughly current.*

---

## 1. What we're building

A small fleet of simulated robots that **negotiate with each other** to share
physical space, where each robot is a separate networked service running its own
LLM agent.

The recurring situation: two or more robots need to pass through a corridor that
fits only one at a time. Each robot privately knows something about its own
situation — battery level, cargo, deadline — that the others cannot see. They
have to talk to work out who goes first.

**North star:** a fleet of independently deployed agents that coordinate well
enough to beat a naive first-come-first-served baseline, with a measurable
number attached to how well they do it.

This is a real problem, not a toy one. Mutual exclusion over shared space and
the deadlocks that come from getting it wrong are genuine issues in warehouse
robotics and autonomous driving.

---

## 2. Why this project

| Learning goal | How this project delivers it |
|---|---|
| **Agent-to-agent protocols** | Coordination between separate entities *is* the problem here, not a structure bolted on. We build a working system with homemade messaging first, feel its limits, then adopt A2A and understand why each part of it exists. |
| **Prompt / agent design** | The interesting variable is the negotiation protocol: what agents may say, what they must commit to, what they're scored on. Every experiment is a design experiment. |
| **Cloud infra** | Infra is introduced one layer at a time, each layer triggered by a concrete pain. No cargo-culting. See §5. |

Secondary: it's in robotics/AV, which is the thing I actually want to spend
months of evenings on.

---

## 3. The one design principle

> **Agents must have different information or different objectives. Otherwise
> you've built a chain, not a multi-agent system.**

The test to apply at every decision: *would this work better as a single prompt
with all the context?* If yes, the design is wrong. The fix is almost always to
take information **away** from someone.

Concretely, this means we will resist several tempting simplifications:

- Giving every robot a full view of the world state. If they all know
  everything, there is nothing to negotiate about.
- Routing all messages through a central coordinator that decides. That's a
  dispatcher with extra steps.
- Scoring only the fleet's collective outcome. Some tension between individual
  and collective incentives is what makes the negotiation real.

### 3.1 An agent is an identity + an interface + a policy

A2A exists to connect **opaque** agentic applications — opacity is the design
goal, not a side effect. Nothing outside an agent knows how it decides.

So a robot's policy is pluggable:

| Policy | Use |
|---|---|
| `LLMPolicy` | the research subject |
| `AlwaysYieldPolicy` / `NeverYieldPolicy` | baselines, and adversarial peers |
| `PriorityByIdPolicy` | the naive first-come-first-served baseline |
| `ScriptedPolicy` | says the same thing every run — a control group |
| `HumanPolicy` | you, typing into a terminal |

**The harness is policy-agnostic. The research question is about one particular
policy.** Keeping those separable is what makes the project extensible.

Four consequences, all practical:

1. **Baselines are not a separate code path.** FCFS is just another agent on the
   same interface — same world, same scenarios, same metrics, same plumbing.
   Only the policy differs, which removes a whole category of "is this
   comparison fair?" doubt.
2. **Mixed fleets are a free experiment.** Two LLM robots plus one that never
   yields: how do reasoning agents cope with a peer that won't negotiate?
3. **The infra phases cost nothing to build.** Phases 3–9 can be developed and
   tested entirely against deterministic policies — microsecond responses,
   reproducible, no API key, millisecond test suites. LLMs then arrive into
   infrastructure that already works. **This is the recommended order.**
4. **Debugging gets a control group.** Swap one agent for a scripted one and
   the other variables hold still.

**Where the abstraction leaks** — design for both cases from the start:

- **Latency.** Deterministic policies reply in microseconds, LLMs in seconds.
  Timeouts, retries, and whether the world pauses for deliberation must be built
  for the slow case, or everything will pass with stubs and collapse when a real
  model appears.
- **Language.** A rule-based policy cannot parse free-form prose. This pressure
  is a gift: it forces a **structured message schema** —
  `{intent, claim, commitment, free_text?}` — where the machine-readable part
  carries the decision and the prose is commentary. Better protocol, easier
  evals, and every policy type can participate.

---

## 4. Where we end up

```
                        ┌──────────────────────┐
                        │   World / Simulator  │  stateful, long-lived
                        │  (grid, physics,     │  laptop → small VM
                        │   collision, ticks)  │
                        └───────────┬──────────┘
                                    │  observations / accepted plans
              ┌─────────────────────┼─────────────────────┐
              │                     │                     │
       ┌──────┴──────┐       ┌──────┴──────┐       ┌──────┴──────┐
       │  Robot A    │◄─────►│  Robot B    │◄─────►│  Robot C    │
       │  agent      │  A2A  │  agent      │  A2A  │  agent      │
       │             │       │             │       │             │
       │ own identity│       │ own identity│       │ own identity│
       │ own secrets │       │ own secrets │       │ own secrets │
       └─────────────┘       └─────────────┘       └─────────────┘
              each: one Cloud Run service, one service account

       ┌────────────────────────────────────────────────────────┐
       │  Registry (who exists, agent cards)  ·  Firestore      │
       │  Broadcast (announcements)           ·  Pub/Sub        │
       │  Traces (who said what, when)        ·  Cloud Trace    │
       └────────────────────────────────────────────────────────┘
```

**Critical architectural constraint — the layer split.** An LLM call takes
1–5 seconds. A robot control loop runs at 10–100 Hz. These cannot be the same
thing. So:

- **Reactive layer** — deterministic, fast, lives in the simulator. Collision
  avoidance, movement, "don't drive into a wall." No LLM ever.
- **Executive layer** — plain code executing an agreed plan. "Wait at the
  corridor mouth until B clears it, then proceed."
- **Deliberative layer** — the LLM agents, running at roughly 0.1–1 Hz. They
  negotiate and produce plans. **They never touch the control loop.**

Getting this wrong is the single most common way LLM-robotics projects fail.
Building the simulator in *steppable* mode — it can pause and wait for
deliberation — makes this easy while developing.

### 4.1 What is and isn't an LLM

**Only the robots have LLMs. The simulator is deterministic code with no model
in it anywhere.**

But the simulator is not a passive notifier — it is the **referee**, and it owns
five jobs:

1. Holds ground-truth world state
2. Advances time in ticks
3. Validates actions — accepts or rejects a move, resolves collisions
4. **Decides what each robot is allowed to see**
5. Detects outcomes (deadlock, task complete) and logs episodes for metrics

Job 4 is the most consequential piece of non-LLM code in the project, because
**the information asymmetry is produced by that function.** In Phase 1 it's
faked by hand-writing two different system prompts. From Phase 3 on, the
simulator computes each robot's observation from its position, sensor range and
private state. Changing that function changes what there is to negotiate about.

### 4.2 Two channels, kept separate

| Channel | Between | Carries | Protocol |
|---|---|---|---|
| **World** | robot ↔ simulator | observations down, actions up | MCP (agent → tool) |
| **Negotiation** | robot ↔ robot | proposals, claims, commitments | A2A (agent ↔ peer) |

**Negotiation traffic never passes through the simulator.** If it does, we've
rebuilt a central coordinator and lost the point of the project. The protocol
split is not decoration: a peer you bargain with and a tool you query are
genuinely different relationships, and the two specs reflect that.

**MCP is vertical, A2A is horizontal.** MCP connects an agent to something
passive that executes what it is told — the simulator cannot refuse to hand over
the map. A2A connects peers that each have goals and can say no — a robot
absolutely can refuse to yield.

Practically, the simulator becomes an MCP server exposing `get_observation()`,
`propose_move()`, `get_map()`, and each robot is an MCP client. The payoff lands
at Phase 11: swapping the text grid for a real simulator means writing a new MCP
server with the same tool names, and **the agents don't change at all.** That is
the N×M → N+M benefit arriving in our own repo.

### 4.3 A robot is not purely an LLM either

Each robot service is an LLM **plus a small deterministic state machine**:

- The **LLM fires only when there is a conflict to resolve** — a contested
  corridor — and outputs a commitment: *"wait until B clears, then proceed."*
- The **state machine executes that commitment** across many ticks with no model
  calls at all.

**Most ticks involve zero LLM calls anywhere in the system.** A model call every
tick means something has gone wrong, and it will show up immediately as cost and
latency.

---

## 5. The phases, and the infra each one introduces

Each phase leaves a working thing. Any phase is a fine place to stop for a
while. Times assume evenings and weekends, not full-time.

### Phase 1 — Two agents, one conversation
**~3h · Infra: none**

Two robots with private situations, talking in one Python process. No grid, no
movement, no network. Printed to the terminal.

*Why no infra:* you cannot make good infra decisions about a system whose
behaviour you haven't seen yet.

### Phase 2 — Make it measurable
**~3h · Infra: none (a CSV file)**

Agents must commit to a decision (`DECISION: A`). Run 20 seeded scenarios,
record: did they agree, how many messages, did the genuinely more urgent robot
win. This produces the **first number**, and every later phase is judged against
it.

*Why this comes before everything else:* without a metric, all later work is
vibes. This is the phase that's tempting to skip and shouldn't be.

### Phase 3 — Add the world
**~5h · Infra: none**

A grid, a corridor, robots that actually move. The negotiated decision now
causes something to happen, and failure looks like a real deadlock on screen.
Establishes the three-layer split from §4.

Baseline to beat: first-come-first-served with no communication at all.

### Phase 4 — Split into separate processes
**~5h · Infra: FastAPI, two terminals**

Each agent becomes its own HTTP service on localhost, with **homemade**
messaging. Deliberately homemade.

*What this teaches:* serialization, what a "message" actually is, what happens
when one agent is slow, and what happens when one is simply *down*. These
questions have no meaning inside a single process.

### Phase 5 — Adopt A2A
**~6h · Infra: `a2a-sdk`, still localhost**

Replace the homemade protocol with real A2A: agent cards, task lifecycle,
streaming, webhook callbacks.

*Why in this order:* having already hit the problems A2A solves, the spec reads
as obvious rather than as ceremony. Adopting it in Phase 1 would have been
cargo-culting a protocol we didn't understand.

### Phase 6 — The world as an MCP server
**~4h · Infra: `mcp` (Python SDK), still localhost**

Reconnect the grid to the now-networked robots, over the protocol §4.2 always
described: `world_server.py` exposes `get_map()`, `get_observation(side)`,
`propose_action(side, action)`. The world stays the sole authority on ground
truth and the one thing no single robot can safely decide alone - whether it's
safe to enter the shared corridor zone - but a robot commands its own motors
and decides its own move/wait, the same as a real one would.

*Why this had to happen before more robots or the cloud:* Phases 4-5 split
*negotiation* into real processes but left the grid living only inside the
single-process tools (`simulate.py`) - the networked robots had no world to
move through at all. See D17.

### Phase 7 — Containers
**~5h · Infra: Docker, Docker Compose**

Each agent gets a container; Compose runs the fleet. Nothing behaves
differently, but the local setup now matches what the cloud will run — which is
what makes Phase 8 boring instead of miserable.

### Phase 8 — Deploy
**~1 weekend, plus an IAM tax · Infra: Cloud Run, ~~Secret Manager~~, Vertex AI**

One agent to Cloud Run first, then the rest. Each agent gets **its own service
account**, and agents authenticate to each other with OIDC ID tokens.

*This is the phase that makes the project real,* because agent identity stops
being a name in a prompt and becomes an actual security boundary. Also the phase
to switch from the Anthropic API to Claude on Vertex AI, so everything lives
under one auth system and one bill.

Budget an extra half-day for IAM specifically. Everyone loses time there.

**Actual state (D33, D34, D36):** OIDC agent-to-agent auth done and verified
live (`robot-b` a locked-down Service, `robot-a` a Job). Claude-on-Vertex is
built as a flag (`--vertex`, D34) but **not live-verified** — the fresh
project's `anthropic-claude-sonnet` quota is 0 and the increase was
auto-denied (support ticket open). **Secret Manager was dropped** (D34): on
the Vertex path there's no API key left to protect. A `GeminiPolicy` (D36,
`--policy gemini`) was added as a multi-provider exercise — and since
Gemini's Vertex quota *is* non-zero, it's the way the deployed pipeline gets
exercised while the Claude quota is stuck.

### Phase 9 — Three or more robots — **DEFERRED**
**~6h · Infra: Pub/Sub, Firestore**

Two agents can just talk to each other. Three need **discovery** (who exists?)
and **broadcast** (announcing to everyone at once). A2A is point-to-point, so
this gap is ours to fill: Firestore holds the registry of agent cards, Pub/Sub
carries announcements.

*This is the phase where "multi-agent system" starts being literally true.*

**Deferred by choice.** N robots means a `world.py` rewrite (positions/
collision are pairwise and hardcoded to A/B) plus a real N-way-negotiation
design fork (2 robots decide one binary; 3+ need an *ordering*, with no
central coordinator per §4.2). The discovery/broadcast half only earns its
keep at 3+ robots. Staying at 2 for now. The Firestore *dependency* still
arrives early — via Phase 10b-2 below, for world state, not the registry.

### Phase 10 — See what's happening
**Infra: OpenTelemetry → Cloud Trace**

Every A2A message becomes a span. Debugging a multi-step negotiation from raw
logs is genuinely miserable, and by this point that's what we'd be doing.
Split into sub-phases because 10b turned out to be three separate pieces:

- **10a — negotiation-path tracing (local). DONE (D35).** `agent.py --trace`,
  off by default via a stdlib-only `src/tracing.py` no-op shim.
  `negotiation.episode` → `negotiation.turn` → `llm.respond` (model, token
  counts) on the initiator, `negotiation.handle` on the responder, plus
  a2a-sdk's own task-lifecycle spans (free once a `TracerProvider` exists).
  httpx + FastAPI instrumentation links the two robot processes into one
  trace. Verified live, including on the Gemini-via-Vertex path.

- **10b-1 — MCP world-path tracing (local). DONE (D37).**
  `world_server.py --trace` → `world.get_observation` / `world.propose_action`
  spans; `agent.py`'s `run_robot` loop → `world.episode` → `world.tick` →
  `mcp.<tool>`; `--webhook` initiator gets its `negotiation.episode` span
  (the one D35 skipped). Verified live across 3 processes — the world's
  handling nests into the calling robot's trace. `tracing.span()` is now
  dual-protocol (`with` and `async with`). No new infra.

- **10b-2 — Firestore-backed world (Option C). DONE (D38).**
  `world_server.py`'s in-memory `STATE` is now one of two backends behind
  `world_store.py`; `InMemoryWorldStore` stays the default (tests +
  single-process runs unchanged), `--firestore` swaps in
  `FirestoreWorldStore` — positions in one doc `world/current`, `propose()`
  a `@firestore.transactional` read-modify-write. New `reset_world()` tool
  + `--reset` flag for episode lifecycle. Verified live against real
  Firestore (`(default)` DB in `us-central1`): standalone store exercise +
  a full 3-process `--firestore` episode, 18-entry log persisted in
  `world/current`.

- **10b-3a — the deployed negotiation, on Gemini, traced. DONE.** No code
  changes: `robot-a` (Job) and `robot-b` (Service) redeployed with
  `--policy gemini --vertex-project corridor-agents --trace` +
  `OTEL_TRACES_EXPORTER=gcp` (both SAs +`roles/cloudtrace.agent`). A real
  cloud-to-cloud Gemini negotiation; Cloud Trace shows one trace across both
  services with `llm.respond` (`provider=gemini`) and the cross-process
  `POST` → `POST /` link.

- **D39 — `visualize_network.py --firestore`. DONE.** The network replay
  reads the whole episode from `world/current` (grid from `world.py`
  constants, movement log from D38, negotiation transcript from a new
  `negotiation` field written by a `record_negotiation` MCP tool), so a
  replay works for a Cloud Run episode with nothing local to query. Verified
  against a local `--firestore` Gemini episode.

- **10b-3b (code). DONE (D40).** `agent.py --serve` keeps the process alive
  after `reached_target` — `run_robot`'s loop idles on `wait_for_reset` until
  `reset_world()` starts a fresh episode. `--auth` now also carries an OIDC
  token on the MCP world channel (`build_world_client`). New
  `trigger_episode.py` starts an episode. Verified 3-terminal: two `--serve`
  robots, 3 episodes, no restart.

- **10b-3b-ii — deploy. DONE (D41), one loose end.** Three Cloud Run
  Services: `world` (`world-server@` SA, `roles/datastore.user` +
  `roles/cloudtrace.agent`, `--firestore --trace`, scale-to-zero), `robot-a`
  and `robot-b` (`--world-url --serve --auth --trace`,
  `--min-instances=1 --no-cpu-throttling`); `robot-a` converted Job→Service;
  `roles/run.invoker` for the robot SAs on `world` and mutually. Verified
  live: `trigger_episode.py --auth` → clean episode (`routine_vs_medical`,
  Robot B wins, `a=8 b=1`), the `--serve` lifecycle in robot-a's logs, and
  **one ~307-span Cloud Trace across all three Services**.

  **A pre-visualizer review then found three deployed-path defects** (the
  broken render turned out to be downstream of bad data, not a template
  bug):
  1. **Token expiry crash-loop — FIXED (D42).** The `--auth` OIDC token was
     fetched once at startup and never refreshed; the robots 401'd and
     `exit(1)`'d on a ~1h cycle. `WorldChannel` now builds a fresh
     authenticated client per call and retries. Verified live 3-terminal
     (kill/restart the world under two `--serve` robots); redeploy pending.
  2. **`propose_action` is not idempotent — FIXED (D43).** A re-sent MCP
     call applied a second real move (the 0.28s double-step). The world now
     enforces a per-side 0.75s minimum move interval (`too_fast` →
     resolved `wait`, nothing applied); `WorldChannel` no longer retries
     `propose_action` (a failed one is re-proposed next poll). Unit +
     local-verified; deploys with #3 (needs the `world` Service).
  3. **`record_negotiation` had no episode guard — FIXED (D44).** A late
     write from the livelocked episode 1 clobbered `world/current`, leaving
     a fresh-log/stale-transcript mix. `reset_world()` now mints an
     `episode_id`; `get_observation` returns it; every robot→world write
     carries it and mismatches are refused. Plus a per-move `nonce` (true
     exactly-once `propose_action`, closing D43's residual) and
     `record_negotiation` re-stamping `resolved_at` on the world's clock.
     `one_episode` restarts on a mid-run id change. 146 tests;
     local-verified.

  D43 + D44 deploy together (all three Services). Then the
  `visualize_network.py --firestore` render, against clean data.

### Phase 11+ — The actual project, indefinitely

Protocol experiments, a few hours each:

- Add a cost to communication. Do they get more concise?
- Model radio as a *physical* resource — range limits, packet loss — so
  communication itself becomes something robots must reason about.
- Add a **scenario designer**: an LLM agent that is not a robot, adversarially
  constructing situations to break the fleet. Lives outside the simulator; the
  simulator stays dumb.
- Score each robot only on its own outcome. Do they start exaggerating?
- Kill a robot mid-negotiation. Does the fleet recover?
- Let a robot refuse to disclose. Does trust emerge over repeated encounters?
- Replace the grid with a real simulator, or a ROS 2 bridge, or a cheap rover.

**Phases 1–10 are the substrate. Phase 11 is the project.**

---

## 6. Infra decisions

### Where do the agents run? → **Cloud Run, one service per agent**

| Option | Verdict |
|---|---|
| **Cloud Run** ✅ | Scales to zero, so idle costs roughly nothing. Each service gets its own URL and its own service account — a real identity boundary per agent, which is exactly the interesting part. Native HTTPS and SSE, which A2A needs. Container-based, so local Compose and cloud run the same image. |
| **Vertex AI Agent Engine** ❌ *for now* | Managed sessions and memory are nice, but it bills continuously (~$0.086/vCPU-hour, so one always-warm vCPU is ~$60/month before any model calls, plus ~$0.25 per 1,000 session events) and it abstracts away precisely the A2A plumbing this project exists to learn. Worth revisiting at Phase 11+ if its Memory Bank becomes appealing. |
| **GKE** ❌ | A cluster costs money 24/7 and adds Kubernetes to a project that already has enough new concepts. |
| **A VM running everything** ❌ | Cheap and simple, but it collapses the agents back into one box and loses the per-agent identity that makes the exercise worthwhile. |

### Where does the simulator run?

The simulator is **stateful and long-lived**, so it does not fit scale-to-zero.
It stays on the laptop for a long time — probably through Phase 10. When it needs
to run unattended, it goes on a single small spot VM. Keeping the sim local
while the agents are in the cloud is a perfectly good intermediate state, and
it's also a nice forcing function for making the agents genuinely network-based.

### How do we reach the model?

- **Phases 1–6:** the Anthropic API directly. One environment variable. Adding
  GCP auth this early buys nothing.
- **Phase 8 onward:** Claude on Vertex AI. Once everything else is GCP, one auth
  system and one bill is worth the small migration.
- **Model choice:** `claude-sonnet-5` for negotiation. Evals multiply calls fast
  (agents × rounds × episodes), so keep the loop cheap and reserve anything
  bigger for the parts that need it. `claude-haiku-4-5-20251001` is the fallback
  if eval costs bite.

### State and messaging

- **Firestore** — task state, agent registry, episode results. Serverless,
  free-tier-friendly, no instance to keep warm.
- **Pub/Sub** — broadcast announcements, from Phase 9 when there are 3+ agents.
- **A2A webhooks** — for negotiation rounds that outlive an HTTP request. Better
  than holding an SSE connection open for minutes.
- **Secret Manager** — API keys. Never in the image, never in env vars in git.

### Phase 11 infra: three workloads that are not services

The agents never move — Cloud Run, one service each, from Phase 8 onward. What
changes at Phase 11 is everything *around* them.

**1. Eval sweeps → Cloud Run Jobs.** 200 episodes × 5 agents is batch work with
a beginning and an end, not a service waiting for traffic. Same container, run
to completion, fanned out across parallel tasks.

But: **at eval time we do not want the network.** Real HTTP between agents adds
seconds per exchange, adds flakiness, and hurts reproducibility — all poison for
a clean number over 200 episodes. D7 makes this free: because policy is separate
from transport, the *same agent code* runs

- **in-process** for fast sweeps, and
- **over A2A** for realistic integration tests.

Two execution modes, one implementation. **Build this deliberately from Phase 3
rather than discovering it later.**

**2. The world → keep it a pure function.** `step(state, actions) → new_state`,
with state in Firestore rather than process memory. Then the world is stateless,
runs on Cloud Run, scales to zero, and yields two properties that matter more
than they sound: **any episode replays from any tick**, and **parallel episodes
cannot contaminate each other**. Reproducibility stops being a fight.

This breaks the moment a heavy 3D simulator arrives (CARLA, Isaac) — those hold
large internal state and need a GPU, which means a GCE spot VM per experiment.
Cloud Run does offer GPUs that scale to zero, but that doesn't help: the problem
is statefulness, not the GPU.

**Opinion: at Phase 11, scale episodes, not fidelity.** The research question is
about negotiation. A photorealistic simulator costs GPU-hours and teaches
nothing extra about agents reaching agreements, while a cheap grid runs a
thousand episodes for the price of a coffee. The 3D sim is a demo feature — do
it eventually, but never let it become the reason experiments stop running.

**3. Real robots → split by latency.** The reactive layer runs *on* the robot
(motor control, collision avoidance — it cannot depend on a network round trip).
The deliberative agent stays in Cloud Run; 1 Hz tolerates cloud latency fine.
The robot makes outbound connections only, so nothing needs inbound access to a
home LAN.

**Region:** put Cloud Run, Vertex AI and Firestore in the same region —
cross-region hops add latency to every model call and egress cost to every
message.

### What runs where, at the end

| Component | Where | Why | Idle cost |
|---|---|---|---|
| Robot agents | Cloud Run, 1 service each | Own identity, scale to zero | ~$0 |
| Simulator | Laptop, later a spot VM | Stateful, long-lived | ~$0 / ~$10 mo |
| Registry + task state | Firestore | Serverless, tiny data | ~$0 |
| Broadcast | Pub/Sub | Needed at 3+ agents | ~$0 |
| Traces | Cloud Trace | Multi-agent debugging | ~$0 |
| Secrets | Secret Manager | Not in git | ~$0 |
| Model | Anthropic API → Vertex AI | Reasoning | pay per call |

---

## 7. What we are deliberately NOT doing yet

Keeping this list explicit is what keeps the project alive:

- ❌ Any cloud anything before Phase 8
- ❌ Docker before Phase 7
- ❌ A2A before Phase 5 — homemade first, on purpose
- ❌ A real simulator (CARLA, Gazebo, Isaac) — a text grid is enough for a long time
- ❌ ROS 2 — valuable and career-relevant, but Phase 11+
- ❌ Real hardware
- ❌ A web UI or visualizer — this is a reward, not a prerequisite
- ❌ An optimal-solver baseline — nice eventually, not needed to start
- ❌ Refactoring into a clean package structure before Phase 4

---

## 8. Cost

- **Phases 1–6:** model tokens only. A few dollars total, if that.
- **Phases 7–9:** roughly **$0–5/month** in GCP. Cloud Run at zero traffic is
  free, Firestore and Pub/Sub at this volume are inside the free tier.
- **Phase 11+:** model tokens dominate, driven by how often the eval suite runs.

**The real cost risk is eval cost, not infra cost.** Five agents × several
rounds × 50 episodes is thousands of model calls per experiment. If one run
costs $15, the runs stop happening and the project quietly dies. Mitigations
from the start: a small model in the negotiation loop, prompt caching, and
keeping the default episode count low with a `--full` flag for the big sweep.

---

## 9. How we know it's working

Metrics, established in Phase 2 and tracked forever after:

- **Agreement rate** — how often do they reach a decision at all?
- **Correctness** — did the genuinely more urgent robot go first?
- **Efficiency** — messages exchanged per resolution.
- **Deadlock rate** — from Phase 3, when movement is real.
- **Honesty** — from Phase 11, when incentives are individual: how often does a
  robot's claim about itself match its actual private state?

Every metric is measured over **seeded, repeated** scenarios. LLM
non-determinism means a single run tells you nothing, and chasing phantom
improvements is a real way to waste months.

---

## 10. Assumptions and open questions

**Assumptions** (correct any that are wrong):

- Python, since A2A, ADK, and the robotics ecosystem all center on it.
- A GCP account exists or can be created; the free tier covers Phases 7–9.
- Evenings and weekends, not full-time.
- Comfortable with Python and the command line; **not** yet comfortable with
  cloud infra, containers, or distributed systems — hence introducing them one
  at a time.

**Open questions to resolve before Phase 4:**

1. Does the corridor stay the scenario, or does it become an intersection?
   Intersections have richer conflicts but more rules to encode.
2. Does a robot's negotiated commitment bind it? If a robot agrees to wait and
   then moves anyway, is that a bug or a research finding?
3. Do robots have persistent identity across episodes? Reputation only becomes
   possible if B remembers that A lied last time — a big and interesting fork.

---

## Appendix A: mapping to ordinary service engineering

Most of this project is normal distributed systems work with new nouns. Roughly
85–90% of what gets built here is service engineering, and existing instincts
transfer intact.

| New vocabulary | What it actually is |
|---|---|
| Agent | A service |
| Agent card | A service descriptor at a well-known URL — WSDL, OpenAPI, Consul |
| A2A | JSON-RPC 2.0 over HTTPS + SSE for streaming + webhooks for async |
| Task lifecycle | An async job API: submitted / working / completed / failed |
| MCP | A driver interface for tools — ODBC, roughly |
| Multi-agent system | Choreographed microservices, or an actor system |
| Agent memory | A database |
| Tool call | An RPC |
| Orchestrator agent | A workflow engine whose DAG is chosen at runtime |
| Eval suite | A test suite returning a score rather than pass/fail |

The ideas are older than the vocabulary: Contract Net (the bidding protocol this
project uses) is from **1980**, the actor model from **1973**, and FIPA-ACL —
an agent communication language standard from **1997** — defined message
*performatives*: `inform`, `request`, `propose`, `accept-proposal`,
`reject-proposal`. The `{intent, claim, commitment}` schema in §3.1 is
essentially FIPA performatives rediscovered. Worth reading the old literature.

### What is genuinely different

Concentrated in the hardest parts:

1. **The contract is deliberately under-specified.** A REST endpoint has a
   schema — violate it, get a 400. An agent interface is partly prose. The
   dominant failure mode shifts from *"it errored"* to *"it returned 200 and was
   confidently wrong."* This is the whole reason evals exist as a discipline:
   you cannot assert your way out, you must measure distributions.
2. **Non-determinism.** Same input, different output. Caching, retries (a retry
   may return a *different* answer — is that acceptable?), regression tests, and
   reproducing a bug all need rethinking.
3. **Behavior lives in config, not code.** The logic is a string. Prompt
   versioning and rollback become infrastructure concerns.
4. **Cost and latency are ~1000× off.** An internal service call is 1–10ms and
   free; an agent call is 1–10s and costs money. You cannot be chatty. This is
   why §4.3 fires the LLM only on conflict — an architectural constraint imposed
   by economics.

### One new security class

Agents act on natural language input, so a message from a peer can redirect
behavior — **data becomes control**. Conceptually like SQL injection, but with
no parameterized query to fall back on. Relevant from Phase 9, when three robots
are negotiating and one could in principle talk another into something. For this
project that is either a vulnerability or the most interesting experiment on the
list.

**The summary worth remembering:** agents do not make distributed systems
problems go away. They add non-determinism to a system that already had partial
failure.

---

## Appendix B: GCP glossary

Product names for familiar things. The through-line: **everything chosen here
scales to zero**, because a hobby project is idle ~99% of the time. Renting a VM
plus a Postgres instance would cost ~$40/month for nothing to happen.

### Cloud Run

Somewhere to run code that turns itself off when nobody is using it. Hand it a
container, get back an HTTPS URL. It starts a copy when a request arrives and
shuts down when traffic stops.

- *Alternative:* a VM you pay for 24/7 and have to patch.
- *Equivalent to:* Heroku, Fly.io, AWS App Runner. Like Lambda, but runs any
  container rather than a restricted runtime.
- *Here:* one service per robot — one URL, one identity. Robot A negotiates with
  Robot B by calling Robot B's URL.

### Firestore

Somewhere to put data without running a database server. Managed NoSQL document
store — save JSON-shaped documents, read them back. No instance, no connection
pool, no migrations.

- *Alternative:* Postgres — a better database, but needs an always-on instance
  at $10–25/month even while idle.
- *Here:* the agent registry, episode results, and world state between ticks if
  the world is built as a pure function.

### The rest

| Name | What it is | Used for |
|---|---|---|
| **Cloud Run Jobs** | Cloud Run for tasks that finish, not services that wait | Eval sweeps (Phase 11) |
| **Pub/Sub** | A message queue — one publisher, many receivers | Broadcast announcements (Phase 9) |
| **Secret Manager** | Somewhere for API keys that isn't source code | Phase 8 onward |
| **Cloud Trace** | Timeline of what called what, and how long it took | Debugging negotiations (Phase 10) |
| **Vertex AI** | Google's ML platform | Calling Claude from inside GCP (Phase 8) |
| **Agent Engine** | Managed agent hosting — sessions, memory, scaling | Rejected, see D3 |
| **GCE** | Plain rented VMs, billed by the hour | Only if a 3D simulator happens |
| **IAM** | Who is allowed to call what | Per-agent service accounts (Phase 8) |

**Before the first eval sweep:** set a billing budget alert (~$30). Infra costs
a few dollars a month; the thing that bites is a runaway eval loop burning
$200 in model calls overnight.

### "Gemini Enterprise Agent Platform" vs "Cloud Run" — not a choice

GEAP is an **umbrella brand, not a place**. Asking whether an agent runs in GEAP
or on Cloud Run is like asking whether something runs "in AWS or on EC2." This
project uses both:

| Layer | Our choice | Inside GEAP? |
|---|---|---|
| Model | Claude via Vertex AI | **Yes** — Vertex is part of it now |
| Framework | Plain Python (skipping ADK) | ADK would be; we're not using it |
| Agent loop | Our own ~40 lines | n/a |
| **Hosting** | **Cloud Run** | **No** — general-purpose GCP |
| State | Firestore | No |

**Hosting is the only genuine either/or:** managed runtime *or* Cloud Run *or*
GKE. Resolved in D3. Google's own ADK docs list Cloud Run and GKE as first-class
deployment targets alongside the managed runtime, so this is not going against
the grain.

**It is also reversible.** The agent is a container that speaks HTTP; moving to
the managed runtime later changes deployment, not code — as long as the agent
stays framework-agnostic, which D7 already enforces.

### A note on "agent engine"

The term means two different things, which is why it's confusing.

**Generically**, an agent engine is just the loop that drives an agent:

```
while True:
    response = model(conversation, tools)
    if response.wants_to_call_tools:
        conversation.append(execute(response.tool_calls))
    else:
        return response
```

Roughly 40 lines. Every framework — ADK, LangGraph, CrewAI, the Claude Agent
SDK — is that loop plus retries, streaming, parallel tool calls and error
handling.

**As a product**, it's Google's managed agent hosting. The name has churned:
Reasoning Engine → Vertex AI Agent Engine → absorbed into **Gemini Enterprise
Agent Platform** in 2026, where the hosting piece appears as "Deployments" or
"Agent Runtime" depending on the page. Most writing still says "Agent Engine."

Three layers get mushed together, and vendors have an interest in selling them
as one thing:

| Layer | Question | Examples |
|---|---|---|
| Framework / SDK | How do I write the agent? | ADK, LangGraph, Claude Agent SDK |
| Agent loop | What drives model↔tool cycles? | usually part of the framework |
| Hosting platform | Where does it run, who holds its state? | Agent Engine, Bedrock AgentCore, Cloud Run |

**For this project we need almost no engine.** A robot receives an observation
and, if there is a conflict, negotiates and commits to a plan — a small state
machine with occasional model calls, not open-ended tool use. Adopting a
framework to obtain a while-loop would buy someone else's opinions about memory
in exchange for code we can write in an afternoon. Revisit at Phase 11 if robots
start needing genuine multi-step tool use.

---

## Appendix C: repo layout

```
corridor-agents/
├── docs/
│   ├── PLAN.md          ← this file
│   └── DECISIONS.md     ← why we chose things, appended as we go
├── src/                 ← the actual system (empty until Phase 1)
├── experiments/         ← scenario definitions and result CSVs
└── sketches/            ← throwaway spikes, not part of the design
    └── step1_conversation.py
```
