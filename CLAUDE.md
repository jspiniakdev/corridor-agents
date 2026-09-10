# Corridor Agents — working notes for Claude

## Read these first

- `docs/PLAN.md` — the project, the phases, the infra reasoning. The source of truth.
- `docs/DECISIONS.md` — why things were chosen, and what would change our mind.
- `docs/SETUP.md` — the working environment, and gotchas already hit.

These were written in a long design conversation that this session did not see.
Everything decided there lives in these files; treat them as the record.

Do not propose architecture that contradicts a decision in `DECISIONS.md` without
saying which decision it contradicts and why it should be revisited.

## Current state

**Phase 3 complete.** Two robots negotiate over a real 1D grid
(`src/world.py`, run via `simulate.py`) and their decision causes actual
movement, with collision structurally impossible (the reactive layer
re-derives it independently every tick). Negotiation only fires on a genuine
standoff - a robot at its boundary that can sense the other, even before the
other arrives - everything else resolves for free with zero LLM calls.
`world_eval.py`/`experiments/world_cases.csv` batch-measure grid episodes,
alongside `eval.py`/`experiments/cases.csv` (Phase 2) for plain negotiations.

**Phase 4 complete** (superseded by Phase 5 - kept for history). Each robot
ran as its own process negotiating over a deliberately dumb message board
(`comms_server.py`). See D14.

**Phase 5 complete.** Real A2A, peer to peer - `comms_server.py` is gone,
there's no third process at all anymore. Each `agent.py` process is either
a pure A2A client (`--side a`, the initiator, dials out) or a pure A2A
server (`--side b`, the responder, hosting
`agent_executor.NegotiationExecutor`). One negotiation is one A2A task;
`negotiation.py` is completely unchanged - only the transport around it
did. Three things got built this phase: plain request/response (agent
cards + task lifecycle, verified against `run.py`'s baseline exactly),
streaming (a `TASK_STATE_WORKING` heartbeat so a slow peer doesn't look
like a dead one), and webhooks (`--webhook`: the initiator registers a
callback and stops holding the connection open - the one piece that makes
Robot A a server too, not just a client). See D15.

**Phase 6 complete, including dynamic initiation and the network
visualizer** (both were deferred mid-phase, then built as follow-ons in
the same session). The world is a real MCP server (`world_server.py`) -
`get_map()`, `get_observation(side)`, `propose_action(side, action)`,
`get_log()`. `world.py`/`simulate.py` are completely untouched -
`world_server.py` reuses their `WorldState`/`reactive_filter`/`apply`
directly. `propose_action` is synchronous, no independent clock - the
world's authority narrows to exactly the one thing no single robot can
safely decide alone: is it safe to enter the shared corridor zone right
now. See D17.

**Dynamic initiation (D18, D27, D30):** `--side a`/`--side b` no longer
means "client-only" vs "server-only" - every `agent.py --world-url`
process always runs its own A2A server *and* its own movement loop *and*
is capable of dialing the peer, the instant its own observation says
it's at the boundary and can sense the other (D12's physical logic,
unchanged). The dial is cancellable (jitter was tried, then removed in
D30 - see below); a genuine race (both sides dial before either sees the
other's incoming call) is resolved by a fixed name tiebreak cancelling
the loser's outbound attempt. The tiebreak *winner* also cancels its own
outbound dial once `on_resolved` tells it the outcome is already known
via the loser's incoming call (D27) - without this the winner could end
up running two concurrent negotiations for one standoff, a crash this
project never observed until D26 reverted to a grid small enough for
real two-sided races to actually happen. Verified live, repeatedly,
including real caught races on both sides of the tiebreak.

**Movement pacing (D30):** a robot advances at most one cell per
`POLL_INTERVAL_SECONDS` (1.0, was 0.2) - the same "how often does a
robot check in with the world" mechanism as always, just recalibrated so
movement doesn't look instantaneous next to a multi-second LLM
negotiation. Jitter (D18's random pre-dial delay, then D23's
distance-aware skip) is gone entirely - removed, not just retuned, once
a poll interval this wide made a jitter window narrower than it
pointless (both sides still act on their very next poll regardless of
the draw), and once it was clear jitter was never load-bearing for
correctness anyway - D18's tiebreak + D27's cancellation already
guarantee a clean single outcome regardless of timing.

**The network visualizer (D19, D20, D22):** `visualize_network.py` is a
new post-hoc tool. Fetches the completed episode's log from
`world_server.py` (`get_log()`, real timestamp per entry), reads
`experiments/results/negotiation_trace.json` if a negotiation happened
(written by whichever robot's `agent.py` process learns the outcome, now
also carrying real `comms_established_at`/`resolved_at` timestamps), and
gets the winner from `negotiation.check_agreement()` directly rather than
inferring it from movement. `visualize_template.html` is shared with
Phase 3's `visualize.py`, not left fully untouched as first planned -
there's no shared clock anymore (D17), so "tick" would have been a lie
for network episodes; the template detects which shape it was given, and
network episodes now also show a distinct "establishing comms" marker
before the negotiation dialogue. **(D25)** consecutive idle "wait/wait"
polling rows are now collapsed into one labeled frame - `LLMPolicy`'s
synchronous API calls (D15) block the negotiating robot's whole process
for several real seconds, during which the *other* robot's process keeps
polling (D30: once per `POLL_INTERVAL_SECONDS`) and logging no-op rows;
without collapsing, a replay could show many identical empty frames
between "establishing comms" and the actual dialogue. **(D28)** a robot that hasn't logged its own
first `propose_action` call yet (its OS process simply started a beat
after its peer's) now shows as "not started," not "wait" - the two used
to be indistinguishable, making a robot that hadn't spoken yet look like
one deliberately holding at its boundary.

**The grid was briefly widened and made asymmetric (D21), then reverted
(D26).** `MIN_POSITION`/`MAX_POSITION` are back to 1/8, `--max-ticks`
defaults back to 30 - that experiment is done. What stuck: `SENSOR_RANGE`
is no longer tuned to grid extremes at all. It's `len(CORRIDOR_ZONE) + 3`
(still 6, same value as before D21, now a physically-motivated formula
instead of a constant computed from `|A_BOUNDARY - B_START|`) - a robot
only senses (and therefore only negotiates with) the other robot once
it's close enough to the shared zone to be a real collision risk, not
from anywhere on the map. `world_eval.py`'s stats after reverting are
byte-identical to the original pre-D21 baseline.

**Timestamp-based negotiation attribution (D22):** the visualizer's
original heuristic for "when did negotiation happen" (first log entry
where the winner moves off its own boundary) broke on the (since-
reverted, D26) asymmetric grid - fixed by having `world_server.py` and
`agent.py` log real `time.time()` and correlating them directly, instead
of inferring timing from movement. Still current. (D23's distance-aware
jitter skip, built alongside this at the time, is gone - jitter itself
was removed entirely in D30.)

**Live observation composition (D24):** a previously-hidden gap - Phase
3's `compose_observation()` (position/sensor facts, D4b's Job 4) was
never ported into the networked path at all. Every LLM negotiation since
Phase 5 ran on static private text alone, with zero awareness of its own
position or how far the other robot actually was. Caught by watching an
LLM's own stated reasoning miss something it should have known. Fixed
with `observation.py`'s `compose_observation_from_dict()` (the dict-based
equivalent, also carrying D23's `other_distance_to_boundary`), wired into
`agent.py`'s `run_robot()` so `me.situation` is recomposed from the live
MCP observation every loop iteration, using the original private text
captured once so it never compounds.

**Raw ground-truth debugging (D29):** every message now carries a real
per-message `time.time()` in `experiments/results/negotiation_trace.json`
(always on, cheap). `agent.py --debug-log` (off by default) writes
`experiments/results/robot_status_<side>.jsonl`, a structured log of a
robot's own real decision points. New `export_timeline_csv.py` merges the
world log, message timestamps, and (if present) the status logs into one
raw, unfiltered `experiments/results/episode_timeline.csv` - one row per
real event, nothing collapsed or forward-filled - built specifically to
debug without going through `visualize_network.py`'s rendered replay at
all, after D22/D25/D28 all turned out to be replay-rendering artifacts,
not real bugs.

**Phase 7 complete: containers (D32).** One shared `Dockerfile`, one
`docker-compose.yml` with three services (`world`, `robot-a`, `robot-b`)
on Compose's default network - `docker compose up` (after `source .env`)
runs the exact same `routine_vs_medical`/`llm` episode the venv-based
three-terminal command does. `visualize_network.py`/`export_timeline_csv.py`
deliberately stay outside any container - dev/debug tools, not fleet
agents - and read the mounted `./experiments` volume afterward. Two real
bugs found only by actually running containers together, not by review
alone: `world_server.py`/`agent.py` needed `--host 0.0.0.0` to bind
reachably at all (both default to it now, still fine for plain local
runs); and a much subtler one - `a2a-sdk`'s `create_client()` builds its
real connection from the *peer's own self-reported AgentCard URL*, not
the `peer_url` string passed to it, so `agent.py`'s AgentCard had always
hardcoded `127.0.0.1` there - harmless on one machine (that genuinely was
correct), fatal across containers. New `--advertise-url` flag (default
`http://127.0.0.1:<port>`, set to the Compose service's own URL in
`docker-compose.yml`) fixes it - kept deliberately separate from
`--host`, since `0.0.0.0` isn't dialable and `127.0.0.1` isn't reachable
cross-container; neither value works for both jobs. Verified live
end-to-end with real containers and real Anthropic API calls - correct
outcome, clean exit, volume mount confirmed, host-side debug tools
confirmed working against the still-running `world` container
afterward.

**Phase 8 in progress: deploy (D33).** `robot-b` runs as a real Cloud Run
**Service** (`--no-allow-unauthenticated`, its own service account,
`--advertise-url` set to its actual HTTPS URL). `robot-a` runs as a
Cloud Run **Job**, not a Service - `--side a` without `--world-url` is a
genuine one-shot process (dial, negotiate, exit; never binds a port),
which is exactly what Jobs are for and exactly wrong for what Services
expect. No world integration yet - this is plain Phase 5-style
negotiation-only mode, deliberately (the plan keeps the simulator local
through Phase 10; connecting a deployed agent back to it is a separate,
not-yet-started piece). `--advertise-url` generalized from D32's
`--advertise-host` (Cloud Run URLs are HTTPS with no port at all, which
`http://{host}:{port}` can't represent) and a new `--auth` flag (fetches
a real OIDC ID token via `get_id_token()`, attached as
`Authorization: Bearer` through `a2a-sdk`'s `ClientConfig.httpx_client`)
make it work. `get_id_token()` has two genuinely different, both
live-verified code paths: real Cloud Run resources use the metadata
server directly; local testing via impersonated ADC credentials needs
the explicit `impersonated_credentials.IDTokenCredentials` wrapper
instead (`fetch_id_token()` alone doesn't understand impersonation and
crashes). Verified live twice - `robot-a` local (impersonated) → `robot-b`
on Cloud Run, and `robot-a` as a real Cloud Run Job (metadata-server
credentials, zero involvement from the laptop) → `robot-b` on Cloud Run
- both real negotiations, correct outcome, and an unauthenticated `curl`
to `robot-b` confirmed rejected (`403`) before either worked.

**Vertex AI added (D34), flag-gated.** A new `--vertex`/`--vertex-project`/
`--vertex-region` on `agent.py` swaps `LLMPolicy`'s client from
`anthropic.Anthropic()` to `AnthropicVertex(project_id, region)` -
authenticates as the calling process's own GCP identity (ADC/impersonation
locally, the service account on Cloud Run), no API key at all. Off by
default: local venv/Compose are unchanged. Model IDs need no translation for
Sonnet 5 (current-generation Vertex models use the bare first-party ID, not
a dated `@` suffix); `--vertex-region` defaults to `"global"`, Vertex's own
recommended region. **Not yet live-verified against the real Vertex API or
redeployed to Cloud Run** - the `anthropic-claude-sonnet` Vertex quota is 0
on this fresh project and the increase was auto-denied (support ticket
open); construction is unit-tested.

**GeminiPolicy added (D36): the negotiation policy, reimplemented against a
second provider.** `--policy gemini` runs Claude's exact negotiation
contract against Gemini via Vertex (`google-genai`, ADC auth), reusing
`--vertex-project`/`--vertex-region` + a new `--gemini-model` (default
`gemini-2.5-flash`). Shared unchanged: the `SYSTEM` prompt,
`message_from_tool_call()`, `_log_raw()`, the `llm.respond` span, and the
entire negotiation loop / executor / wire / world integration. Provider-
specific and nothing else: SDK, tool-schema dialect, forced-tool-use knob,
roles, response shape, token-count fields. This is D7 taken one step
further (an agent isn't tied to a *provider* either) and it de-risks D34's
Bedrock/Foundry "would change our mind" clause. **Verified live** - real
Gemini negotiation, correct outcome, `provider=gemini` on the trace span.
Bonus: Gemini's Vertex quota *is* non-zero here, so `--policy gemini` is a
working way to exercise the full deployed pipeline (10b) while the Claude
quota is stuck.

**Secret Manager: deliberately dropped, not forgotten.** Once Cloud Run
agents use `--vertex`, there's no API key left on that path for Secret
Manager to protect - it would be infra for a secret that no longer exists in
the cloud deployment. Local/Compose keep `ANTHROPIC_API_KEY` via `.env`,
unchanged.

**Phase 10a done (D35): OpenTelemetry tracing on the negotiation path.**
`agent.py --trace` (off by default) emits four semantic spans -
`negotiation.episode` → `negotiation.turn` → `llm.respond` (model, token
counts, stop reason) on the initiator, `negotiation.handle` on the
responder - plus a2a-sdk's own built-in task-lifecycle spans, which appear
for free once there's a `TracerProvider`. `src/tracing.py` is a
stdlib-only no-op until `--trace`, so `run.py`/`eval.py`/tests pay nothing.
Cross-process linking verified: the initiator's `POST` span is the parent
of the responder's `POST /` span, one connected trace across both robot
processes. Exporter from `OTEL_TRACES_EXPORTER` (`console` default, `gcp`
for Cloud Trace).

**Phase 10b-1 done (D37): MCP world-path tracing.** `world_server.py --trace`
→ `world.get_observation` / `world.propose_action` spans; `run_robot`'s
`--world-url` loop → `world.episode` → `world.tick` → `mcp.<tool>`;
`--webhook` initiator finally gets its `negotiation.episode` span too.
`tracing.span()` is now dual-protocol (`with` and `async with`). Verified
live across 3 processes - the world's tool-handling spans nest into the
calling robot's trace. `mcp` (like a2a-sdk) ships its own OTel spans, free
once a `TracerProvider` exists. Still local-only.

**Phase 10b-2 done (D38): Firestore-backed world, behind a store seam.**
`world_server.py`'s in-memory `STATE` singleton is now one of two backends in
`world_store.py`. `InMemoryWorldStore` stays the **default** - `python
world_server.py`, the test suite, single-process runs are all unchanged, no
`google-cloud-firestore` import. `world_server.py --firestore` swaps in
`FirestoreWorldStore`: positions in one doc `world/current`, `propose()` a
`@firestore.transactional` read-modify-write (so two robots on two Cloud Run
instances can't both "enter" a stale corridor). New `reset_world()` MCP tool
+ `--reset` flag for episode lifecycle (the doc persists across restarts
now). `agent.py` unchanged - robots are MCP clients, backend-agnostic.
**Verified live** against real Firestore (`(default)` DB, `us-central1`):
standalone store exercise + a full 3-process `--firestore --reset` episode,
18-entry log persisted in `world/current` and read back.

**Phase 10b-3a done: the deployed negotiation, on Gemini, traced to Cloud
Trace.** No code changes - `robot-a` (Job) and `robot-b` (Service) redeployed
with `--policy gemini --vertex-project corridor-agents --trace` +
`OTEL_TRACES_EXPORTER=gcp` (both SAs granted `roles/cloudtrace.agent`).
`gcloud run jobs execute robot-a` runs a real cloud-to-cloud Gemini
negotiation; Cloud Trace shows one trace spanning both services -
`negotiation.episode`/`turn`/`llm.respond` (`provider=gemini`) on robot-a,
the `POST /` handling + a2a-sdk spans on robot-b, initiator `POST` parenting
responder `POST /`.

**D39: `visualize_network.py --firestore`.** The network replay reads the
whole episode from `world/current` (grid from `world.py` constants, movement
log from D38's array, negotiation transcript from a new `negotiation` field
written by a `record_negotiation` MCP tool). Works for a Cloud Run episode -
nothing local to query, no trace file. `build_episode_data` and the template
are unchanged. Verified: a `--firestore` Gemini episode →
`visualize_network.py --firestore --firestore-project corridor-agents` →
correct replay (`priority: Robot A`, `correct: true`).

**Phase 10b-3b code done (D40).** `agent.py --serve` (with `--world-url`)
keeps running after `reached_target` - `run_robot`'s loop is now
`while True: one_episode(world); if not serve: return; wait_for_reset(world)`,
where `wait_for_reset` idle-polls until `reset_world()` starts a fresh
episode and clears the per-episode state. `--auth` now also authenticates
the MCP world channel (`build_world_client` mints an OIDC token and wraps
the streamable-http transport), so a locked-down deployed world Service is
reachable. New `trigger_episode.py` = the "start an episode" button
(`reset_world` over MCP). Verified live 3-terminal: two `--serve` robots ran
**3 episodes without restarting**, `trigger_episode.py` between each.

**Phase 10b-3b-ii done (D41): the world-integrated path deployed as three
Cloud Run Services.** `world` (`world-server@` SA, `roles/datastore.user` +
`roles/cloudtrace.agent`, `--firestore --trace`, scale-to-zero), `robot-a`
and `robot-b` (both `--world-url --serve --auth --trace`,
`--min-instances=1 --no-cpu-throttling`); `robot-a` converted Job→Service;
`roles/run.invoker` for the robot SAs on `world` and on each other.
Verified live: `trigger_episode.py --auth` → a clean episode (4-msg
negotiation, `routine_vs_medical`, Robot B wins, `a=8 b=1`), robot-a logs
show `reached target → idle → new episode`, and Cloud Trace has **one
~307-span trace across all three Services** (`world.episode`/`tick` →
cross-process `mcp.* → world.*` + the full A2A `negotiation.*`/`llm.respond`
subtree into the peer). **Still open:** `visualize_network.py --firestore`
against the deployed episode - the Firestore read is correct (grid +
transcript + 17-step log all embedded) but the rendered HTML is wrong; bug
is in `_write_html` / the template, under investigation. See `PLAN.md` §5,
`DECISIONS.md` D41.

**D42: WorldChannel - a fresh world MCP client per call, retried, instead
of crashing.** The deployed robots were crash-looping on a ~1h cycle:
`build_world_client` fetched the `--auth` OIDC token once at startup, it
expired after ~1h, every world call then got a Cloud Run 401, and the
unhandled error (a bare `CancelledError` from the dead client's transport
task group, not an `MCPError`) killed the process. `WorldChannel` now
builds a fresh authenticated client per call (`get_id_token` every time, so
the ~1h expiry can't bite; a dead connection is just a failed connect next
call, not a poisoned long-lived client), retries with capped backoff
(unbounded with `--serve`; raises after 3 tries without it -
`trigger_episode.py` too). `mcp_call` / call sites unchanged;
`build_world_client` returns `(client, aclose)`. Companion:
`PYTHONUNBUFFERED=1` in the Dockerfile (Cloud Run was dropping the robots'
`print`s). **Verified live local 3-terminal** (kill -9 the world mid-idle →
robots log retries and stay up → restart + `trigger_episode.py` → rejoin
clean); 133 unit tests. **Deployed** (robot-a rev 00002, robot-b rev 00008,
commit `827b356`) - clean episode + robot stdout now legible in Cloud
Logging; the hourly `exit(1)` should be gone (needs ~1-2h across a token
boundary to fully confirm). See `DECISIONS.md` D42.

**Also flagged in the same review, not yet fixed:** `propose_action` is not
idempotent (a re-sent MCP call = a real extra move - the 0.28s double-step
seen in the deployed episode) and `record_negotiation` has no episode guard
(a late write from a prior episode clobbered `world/current` - the
transcript the visualizer choked on). Planned fixes: a world-side "too
fast" move limiter, an `episode_id` stale-write guard, and making the world
the sole timestamper. See the design memory / `PLAN.md` §5.

**Cost note:** the two robot Services bill ~$15-40/mo combined even idle
(`--min-instances=1`). Delete them between demos - `--min-instances=0` isn't
enough because a scaled-to-zero `--serve` robot can't be woken.

**Phase 9 (three or more robots) is deliberately skipped for now** - it's
a `world.py` rewrite plus a real N-way-negotiation design fork, and the
discovery/broadcast half (Firestore registry, Pub/Sub) only earns its keep
at 3+ robots. Staying at 2 robots.

**Next:** (1) redeploy + verify D42 (WorldChannel) live - the ~1h crash loop
should be gone; (2) the `propose_action` idempotency / `episode_id` guard /
sole-timestamper fixes from the review; (3) *then* fix the
`visualize_network.py --firestore` render to close 10b-3b-ii, on clean data;
(4) Phase 8 - Claude-on-Vertex still **blocked on a GCP quota increase**
(auto-denied, support ticket open); once it clears, `--policy claude` on
Vertex is a policy swap, not an infra change (D36). See `PLAN.md` §5.

## How to run things

```bash
source .venv/bin/activate        # Python 3.13; required in each new shell
python -m pytest tests/ -q       # 126 tests, no API calls, ~0.03s
python run.py                    # one negotiation, deterministic policies
python run.py --a llm --b llm    # needs: cp .env.example .env && source .env
python eval.py                   # measurement sweep, deterministic cases only
python eval.py --full            # also runs the llm-involving cases
python simulate.py               # one grid episode, deliberate, deterministic
python simulate.py --no-deliberate --a llm --b llm  # FCFS baseline vs. negotiation
python world_eval.py             # grid-episode measurement sweep, free cases only
python world_eval.py --full      # also runs the deliberate llm-involving cases
python visualize.py              # tick-by-tick HTML replay of one Phase 3 (single-process) episode

# Phase 5: negotiation only, two terminals, same --scenario in each
python agent.py --scenario <id> --side b --policy always_yield --port 9001
python agent.py --scenario <id> --side a --policy stubborn --peer-url http://127.0.0.1:9001

# same, but the initiator doesn't hold the connection open - it registers a
# callback and waits to be called back instead:
python agent.py --scenario <id> --side a --policy stubborn --peer-url http://127.0.0.1:9001 --webhook

# Phase 6 + D18: three terminals - the world, then both robots. Both sides
# need --port/--peer-url now (every robot can dial and can be dialed).
# --side a starts first by convention (D31) - no correctness requirement
# either order works (D18's dynamic initiation is symmetric) - but
# whichever one starts first gets a real, small process-startup head
# start every time, which showed up as confusing, seemingly-inconsistent
# timing while debugging (D29's CSV export). --side a first just makes
# that head start consistent and named, instead of an unlabeled artifact
# of "b happened to be listed first":
python world_server.py --port 9500
python agent.py --scenario <id> --side a --policy stubborn  --port 9001 --peer-url http://127.0.0.1:9002 --world-url http://127.0.0.1:9500/mcp
python agent.py --scenario <id> --side b --policy always_yield --port 9002 --peer-url http://127.0.0.1:9001 --world-url http://127.0.0.1:9500/mcp

# D40: add --serve to both agents (they idle at their target instead of
# exiting - the Cloud Run Service shape). Start a fresh episode any time with:
python trigger_episode.py --world-url http://127.0.0.1:9500/mcp
# deployed (locked-down world): add --auth to trigger_episode.py and both agents.

# D19: after the episode above finishes, render it (world_server.py must
# still be running - it holds the log)
python visualize_network.py --scenario <id> --a stubborn --b always_yield --world-url http://127.0.0.1:9500/mcp

# D39: same replay, but read from Firestore instead of a live world_server.py
# + local trace file - for an episode that ran with --firestore (locally or
# on Cloud Run). Grid comes from world.py constants; log + transcript from
# world/current. Nothing local needs to still be running.
python visualize_network.py --scenario <id> --a gemini --b gemini --firestore --firestore-project corridor-agents

# D29: raw CSV instead of (or alongside) the HTML replay - same timing,
# world_server.py must still be running. Add --debug-log to each agent.py
# run above first if you also want robot-status rows, not just world +
# messages.
python export_timeline_csv.py --world-url http://127.0.0.1:9500/mcp

# agent.py's --scenario/--policy default to routine_vs_medical/llm (the
# project's go-to demo case) - a bare `python agent.py --side a --port ...`
# now makes real Anthropic API calls and needs .env sourced; pass
# --policy stubborn explicitly for a free/deterministic smoke test.

# Phase 7 (D32): the same fleet, containerized - one command instead of
# three terminals. Needs Docker Desktop (or another local Docker engine)
# running first.
source .env                       # ANTHROPIC_API_KEY, for the default --policy llm
docker compose up --build

# after it finishes, world's container is still up (only the robots exit
# once they reach target) - the same local debug tools work against it
# unchanged, reading experiments/ via the mounted volume:
python visualize_network.py --world-url http://127.0.0.1:9500/mcp
python export_timeline_csv.py --world-url http://127.0.0.1:9500/mcp
docker compose down               # when actually done

# Phase 8/D34: same as the plain Phase 5 two-terminal run above, but the
# --policy llm side calls Claude via Vertex AI instead of the direct API -
# no .env/API key needed, just real GCP credentials (gcloud auth
# application-default login) and a project with Vertex AI enabled.
# BLOCKED: the anthropic-claude-sonnet Vertex quota is 0 on this project.
python agent.py --scenario <id> --side a --policy llm --vertex --vertex-project corridor-agents --peer-url http://127.0.0.1:9001

# Phase 8/D36: --policy gemini - Claude's negotiation policy, run against
# Gemini via Vertex (google-genai, ADC auth, no API key). Works today
# (Gemini's Vertex quota is non-zero, unlike Claude's). Add --trace on both
# sides to see provider=gemini on the llm.respond span.
python agent.py --scenario <id> --side b --policy always_yield --port 9001
python agent.py --scenario <id> --side a --policy gemini --vertex-project corridor-agents --peer-url http://127.0.0.1:9001

# Phase 10a/D35: add --trace to BOTH sides for OpenTelemetry spans of the
# negotiation (episode/turn/llm.respond on the initiator, handle on the
# responder, plus a2a-sdk's own spans). Console by default - noisy but real;
# set OTEL_TRACES_EXPORTER=gcp for Cloud Trace (10b). Off by default costs
# nothing (src/tracing.py is a stdlib no-op).
python agent.py --scenario <id> --side b --policy always_yield --port 9001 --trace
python agent.py --scenario <id> --side a --policy stubborn --peer-url http://127.0.0.1:9001 --trace

# Phase 8 (D33): real Cloud Run - robot-b as a Service (always listening),
# robot-a as a Job (one-shot: dial, negotiate, exit). Needs gcloud CLI,
# a GCP project with billing enabled, and the APIs/service accounts/IAM
# bindings set up once (see D33 for the full list - several fresh-project
# IAM grants aren't automatic anymore and have to be added by hand).
gcloud builds submit --tag us-central1-docker.pkg.dev/<project>/cloud-run-source-deploy/robot-b:latest .

gcloud run deploy robot-b \
  --image us-central1-docker.pkg.dev/<project>/cloud-run-source-deploy/robot-b:latest \
  --region us-central1 --port 8080 --command python \
  --args agent.py,--side,b,--policy,always_yield,--host,0.0.0.0,--port,8080,--advertise-url,<robot-b's-own-url> \
  --service-account robot-b@<project>.iam.gserviceaccount.com \
  --no-allow-unauthenticated

gcloud run jobs create robot-a \
  --image us-central1-docker.pkg.dev/<project>/cloud-run-source-deploy/robot-b:latest \
  --region us-central1 --command python \
  --args agent.py,--side,a,--policy,stubborn,--peer-url,<robot-b's-own-url>,--auth \
  --service-account robot-a@<project>.iam.gserviceaccount.com

gcloud run jobs execute robot-a --region us-central1 --wait   # triggers one real negotiation
```

## The one design principle

**Agents must have different information or different objectives.** If a change
would work just as well as a single prompt with all the context, it's wrong. The
fix is almost always to take information *away* from someone.

## Conventions that matter

- **Policy is pluggable** (D7, extended by D36). Anything that assumes an agent
  is an LLM is a bug — and anything that assumes a *specific LLM provider* is
  too: `LLMPolicy` (Claude) and `GeminiPolicy` (Gemini) share the prompt,
  `message_from_tool_call()`, and the trace span; only the SDK/schema/response
  shape differ. Deterministic policies must stay first-class — they're the
  baselines, the test fixtures, and the adversaries.
- **Messages are structured.** The decision lives in `goes_first`; `text` is
  prose commentary and must never carry the decision, or rule-based policies
  can't participate.
- **Ground truth stays hidden.** `Scenario.urgency` must never reach a prompt.
  There is a test asserting this — keep it passing. A networked robot process
  (`agent.py`) must never hold the full `Scenario` either — only
  `scenarios.for_side()`'s own-half slice (D16). Only the in-process tools
  (`run.py`/`eval.py`/`simulate.py`/`world_eval.py`) legitimately need both
  halves, to score correctness.
- **Latency is designed for the slow case.** Timeouts and retries assume
  LLM-speed responses even when tests use microsecond stubs.
- Prefer boring stdlib code. New dependencies need a reason.

## Pacing

This is a learning project, built one small step at a time. Prefer the smallest
change that leaves a working, runnable thing. Don't build ahead into later
phases — `PLAN.md` §7 lists what is deliberately not being built yet, and that
list is load-bearing.
