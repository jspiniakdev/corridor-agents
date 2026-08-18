# Decision log

Short entries, appended as we go. The point is to remember *why*, so that in
three months we don't undo a choice without knowing what it cost.

Format: what we decided, what we rejected, why, and what would change our mind.

---

## D1 — Build the negotiation before building the world

**Decided:** Phase 1 is two agents talking, with no grid, no movement, no
simulation.

**Rejected:** building the simulator first and adding agents to it.

**Why:** the conversation is the point of the project; the world only exists to
give the conversation stakes. Building the world first is several days of work
before seeing whether the interesting part is interesting at all.

**Would change our mind:** if negotiation turns out to be trivially easy without
physical consequences — i.e. agents always agree instantly and sensibly. Then the
world is doing more work than expected and should come sooner.

---

## D2 — Homemade messaging before A2A

**Decided:** Phase 4 uses hand-rolled HTTP between agents. A2A arrives in
Phase 5.

**Rejected:** adopting A2A from the first networked version.

**Why:** a protocol you adopt before feeling the problems it solves is
cargo-culting. Agent cards, task lifecycle, and push notifications each answer a
specific pain, and the plan is to hit those pains first so the spec reads as
obvious.

**Cost of this choice:** some throwaway work in Phase 4. Accepted deliberately —
it's tuition, not waste.

---

## D3 — Cloud Run, not Vertex AI Agent Engine

**Decided:** each agent is a Cloud Run service.

**Rejected:** Agent Engine (managed sessions and memory), GKE, one shared VM.

**Why:** Cloud Run scales to zero (~$0 idle), gives each agent its own service
account — a real identity boundary rather than a name in a prompt — and speaks
plain HTTPS/SSE, which is what A2A needs. Agent Engine bills continuously
(~$0.086/vCPU-hour, ~$60/month for one warm vCPU, plus ~$0.25 per 1,000 session
events) and hides exactly the plumbing this project exists to learn.

**Would change our mind:** if session/memory management becomes the bottleneck
at Phase 10+, Agent Engine's Memory Bank is worth revisiting for that piece
specifically.

---

## D4 — LLMs never touch the control loop

**Decided:** three layers — reactive (deterministic, fast, in the simulator),
executive (plain code running an agreed plan), deliberative (LLM agents at
0.1–1 Hz).

**Rejected:** letting an agent emit movement commands directly.

**Why:** LLM latency is 1–5s; control loops run at 10–100 Hz. Mixing them is the
most common way LLM-robotics projects fail. The simulator will be steppable so
it can pause for deliberation during development.

**Would change our mind:** nothing. This one is structural.

---

## D4b — The simulator is dumb; negotiation never routes through it

**Decided:** only robots have LLMs. The simulator is deterministic code acting
as referee — ground truth, ticks, action validation, **deciding what each robot
may observe**, and outcome detection. Robot↔world uses MCP; robot↔robot uses
A2A, directly, peer to peer.

**Rejected:** routing negotiation messages through the simulator (convenient,
since it already talks to everyone).

**Why:** a simulator that relays negotiation is a central coordinator wearing a
disguise, and centralized coordination is the thing this project exists to
avoid. Keeping the channels separate also keeps the protocols honest: a peer you
bargain with and a tool you query are different relationships.

**Note:** the observation function — deciding what each robot can see — is the
most consequential non-LLM code in the project. It *is* the information
asymmetry.

**Would change our mind:** modelling radio as a physical resource (range,
packet loss) would give the world a legitimate role in mediating comms. That's a
Phase 10 experiment, not a starting design.

---

## D5 — Anthropic API first, Vertex AI at Phase 7

**Decided:** direct Anthropic API through Phase 6; switch to Claude on Vertex AI
when deploying.

**Rejected:** starting on Vertex AI.

**Why:** one environment variable versus a GCP auth setup that buys nothing
until there's something deployed to authenticate. The migration is small once
the rest of the stack is already GCP.

---

## D7 — Policy is pluggable; build the harness with deterministic agents

**Decided:** an agent is an identity + an interface + a policy. The policy —
LLM, rule-based, scripted, human — is invisible to everything outside the agent.
Phases 3–9 get built and tested against **deterministic** policies; LLMs are
swapped in afterwards.

**Rejected:** treating "agent" and "LLM" as the same thing, and writing the
baselines as a separate non-agent code path.

**Why:** A2A is explicitly a protocol for *opaque* agentic applications, so this
is working with the grain rather than against it. The payoffs are concrete —
baselines run on identical infrastructure (removing "is the comparison fair?"
doubt), mixed fleets become a free experiment, the whole infra ladder costs zero
tokens to build, and scripted policies give debugging a control group.

**Consequences to design for now:**

- **Latency:** timeouts and retries must be built for LLM-speed responses even
  while testing with microsecond stubs, or the system will pass all tests and
  fail on first contact with a real model.
- **Message schema:** must be structured (`{intent, claim, commitment,
  free_text?}`) so non-LLM policies can participate. Free-form prose alone would
  lock out every deterministic policy — and structured messages make evals
  easier anyway.

---

## D6 — Measure before optimizing

**Decided:** Phase 2 produces a metric before the world, the network, or the
protocol exist.

**Why:** LLM non-determinism means single runs are noise. Without seeded,
repeated scenarios, every later "improvement" is unfalsifiable, and months can
go into chasing phantoms.

---

## D8 — Force structured output for LLMPolicy instead of prompt + regex parsing

**Decided:** `LLMPolicy` sends a forced tool call (`tool_choice={"type": "tool",
"name": "respond"}`) with a JSON schema, instead of asking for JSON in the
system prompt and regex/`json.loads`-parsing free text out of the reply. The
schema's `goes_first` field is an enum built per call from the two real robot
names in that negotiation, not a generic string.

**Rejected:** the original approach — `SYSTEM` prompt instructing "reply with
ONLY a JSON object," then `LLMPolicy._parse` pulling a `{...}` blob out of the
raw text with regex, `json.loads`-ing it inside a try/except, and manually
checking `goes_first` against the two known names to drop a hallucinated one.

**Why:** the schema constrains the model's output at the API level instead of
hoping the prompt is followed. Concretely: less code (no regex extraction, no
JSON-decode try/except, no free-text fallback branch), and a hallucinated
third robot name is no longer something to catch after the fact — it isn't a
valid tool call in the first place, because the enum only contains the two
robots actually in this negotiation.

**What's kept anyway:** one defensive line in `_to_message` re-checking that
`goes_first` is one of the two real names before trusting it. The schema
should make a violation impossible, but there's no hard guarantee every
constraint is enforced API-side, and the check costs nothing.

**Would change our mind:** if a future policy needs genuinely free-form
output (not a structured decision plus a `text` field alongside it), forced
tool-use would need to loosen or go away for that policy.

---

## D9 — What to measure is data (`cases.csv`), not code

**Decided:** `experiments/cases.csv` lists every (scenario, policy_a, policy_b,
repeats) combination `eval.py` should run. `eval.py` just reads the file,
skips any row touching the `llm` policy unless `--full` was passed, and runs
the rest. The file's initial ~80 rows were generated once by a throwaway
script (every deterministic pairing × every scenario, plus every
llm-touching pairing × every scenario × 3 repeats) and then committed as a
plain static file — nothing in the codebase regenerates it automatically.

**Rejected:** generating the combinations inside `eval.py` itself (e.g. an
`itertools.product` over policies and scenarios at runtime).

**Why:** what gets measured should be editable without touching code. Adding
one weird pairing, dropping a scenario from the sweep, or bumping an LLM
pairing's repeat count is a spreadsheet edit, not a Python change. It also
means the file can be inspected on its own to see exactly what "the sweep"
currently covers.

**Would change our mind:** if the case list needed to be generated
dynamically from something that changes often (e.g. scenarios themselves
being generated per run rather than hand-authored), a static file would stop
making sense.

---

## D10 — `correct` is `None`, not `False`, when no decision was reached

**Decided:** in `eval.py`'s per-episode result row, `correct` is `None`
whenever `should_go_first` is `None` (a genuine tie — no ground truth to be
right or wrong about) **or** the robots never agreed at all (a deadlock).
Only an actual `agreed_on` decision gets scored `True`/`False` against
`should_go_first`.

**Rejected:** scoring a deadlock as `correct=False` on the reasoning that
"the truly urgent robot didn't get priority."

**Why:** PLAN.md §9 tracks agreement rate and correctness as separate
metrics on purpose. Folding deadlocks into `correct=False` would make the
correctness rate move every time the agreement rate moves, for no new
information — it's already captured by "did they agree" and shouldn't also
silently degrade "was the decision right," which should only be about
episodes where a decision actually happened.

**Would change our mind:** if a future phase specifically wants a metric like
"did the truly urgent robot end up going first, counting deadlock as a loss"
— that would be a new, explicitly named metric, not a redefinition of
`correct`.

---

## D12 — Phase 3: collision prevented structurally, negotiation only on genuine standoff

**Decided:** `src/world.py`'s reactive layer (`reactive_filter`) independently
re-derives whether two proposed moves would put both robots in the corridor
zone at once, regardless of what decided those moves — so collision is
structurally impossible, not merely avoided by trusting the layer above it.
Separately, the deliberative layer (real negotiation) only fires when a robot
at its boundary can currently *sense* the other robot (within
`SENSOR_RANGE`), not only on an exact simultaneous double-arrival. Once
either robot reaches its boundary alone with nothing else nearby, it simply
claims priority and proceeds — zero LLM calls, matching `PLAN.md` §4.3.

**Rejected:** two things.
1. Trusting the executive layer's move/wait decision without independent
   verification — a bug there could otherwise cause a real collision, not
   just a wrong-but-safe outcome.
2. Requiring an exact simultaneous double-arrival to trigger negotiation.
   With the real start positions (A=1, B=8) and `CORRIDOR_ZONE={3,4,5}`, A
   structurally reaches its boundary one tick before B always does — under
   the exact-arrival rule, negotiation *never* fired in a real episode, only
   when both robots were manually placed at their boundaries for a test.

**Why:** the reactive layer's guarantee needs to hold even if the executive
layer is wrong, later, for reasons nobody anticipated - re-deriving
independently from actual positions is what makes it structural rather than
trusted. The sensor-range trigger fixes a real, confirmed bug (via a live
`simulate.py` run) without needing to hand-tune start positions for
artificial symmetry - it generalizes to any starting configuration where the
robots come within sensing range of each other, not just a perfectly
synchronized one.

**Consequence:** `deliberate=False` (FCFS) explicitly does NOT get the
sensor-based broadening - it keeps the old strict rule (only an exact
double-arrival is a real tie). Applying the same broadening there would
corrupt what FCFS means: "no communication, strict arrival order." A robot
that genuinely arrived first should stay the winner under FCFS, not get
overridden by a tie-break rule it didn't actually tie for.

**Would change our mind:** if a future scenario needs robots to reason about
conflicts from much further away than one sensor-range's worth of lead time
— that would need a genuinely different mechanism (e.g. broadcasting intent),
not just a larger constant.

---

## D11 — Drop `REJECT`; disagreeing is just proposing again

**Decided:** `Intent` has three values now, not four - `inform`, `propose`,
`accept`. There is no `reject`. A robot that disagrees with the standing
proposal simply sends a new `propose` naming itself (or whoever it thinks
should go first) instead. `goes_first` is also now a *required* field in
`LLMPolicy`'s tool schema, not just `intent` and `text`.

**Rejected:** the original four-intent design, where disagreeing meant
sending `reject` (with `goes_first` as unstructured flavor, not a real
standing position).

**Why:** `standing_proposal()` only ever tracked `PROPOSE` messages. Under
the old design, a robot that "insisted on itself" via `REJECT`
(`NeverYield`, `Stubborn`, and - naturally, since the prompt offered it as
an option - `LLMPolicy` too) never actually put a new position on the
table. A later `ACCEPT` of that position had nothing valid to match against,
so it silently failed to register as agreement and the negotiation burned
through the remaining turns instead. This was caught directly: `Stubborn`
vs `Stubborn` "agreed" after 6 messages, but on inspection that agreement
was Robot A matching its own six-message-stale first proposal, not
Robot B's actual (rejected-and-reasserted) position - a bug wearing the
costume of a resolved negotiation. The fix removes the second, untracked
way of expressing a position, so there's only one - `PROPOSE` - and
`_check_agreement` (unchanged) always has something real to match against.

**Consequence:** deterministic baselines changed behavior. `Stubborn` vs
`Stubborn` now resolves in 5 messages (was 6) and agrees on Robot B (was
Robot A, incorrectly). The Phase 2 default sweep's numbers moved too:
agreement rate 78% → 89%, since several pairings that used to falsely
deadlock now resolve correctly.

**Would change our mind:** nothing foreseen - `REJECT` added no real
information `PROPOSE` couldn't already carry, since `goes_first` was always
the field that mattered.

---

## D13 — Build a visualizer now, ahead of PLAN.md §7's own schedule

**Decided:** add a single-episode HTML replay (`visualize.py` +
`visualize_template.html`) that animates a grid episode tick by tick,
pausing to show the negotiation dialog when one happens.

**Rejected:** waiting, per `PLAN.md` §7's explicit "not doing yet" list:
`❌ A web UI or visualizer — this is a reward, not a prerequisite`.

**Why we're crossing that line now anyway:** that line was written when
there was nothing worth looking at - Phase 1/2 had no world, no movement,
nothing spatial to animate. Phase 3 changed that: there's now real per-tick
grid state and, when a genuine standoff happens, a full negotiation
transcript. And it costs nothing infrastructurally - a pre-computed replay
(chosen deliberately over a live server) is one self-contained HTML file
with the episode data inlined, no server process, no new dependency,
consistent with every other phase's "no infra until it's earned" pattern.
This isn't the web UI/dashboard §7 was deferring (multi-episode, persistent,
a real frontend stack) - it's a single-episode debugging aid built the same
way everything else in this project has been: boring, additive, zero new
infrastructure.

**Would change our mind:** if this grows into needing a live server, a
build step, or a JS framework to stay useful - at that point it would
actually be the thing §7 deferred, and should wait for whatever phase
Cloud Run/A2A get established, rather than becoming a second, ungoverned
infra track.

---

## D14 — Phase 4: a dumb message board, not a referee that drives negotiation

**Decided:** `comms_server.py` is a stateless, negotiation-unaware append-only
message board (`GET`/`POST /messages`) — it doesn't know what a turn is or
when an agreement happened. Each robot runs as its own process (`agent.py`)
that polls the board and independently computes its own turn from
`len(history) % 2` (the same alternation `negotiate()` already used), checks
`check_agreement()` itself, and speaks using its own local, real policy. No
third party ever makes a negotiation decision.

**Rejected:** the first design that was actually built partway through
planning - a `RemotePolicy` wrapping an HTTP call, plus a `run_networked.py`
driver script that called both robots' endpoints in turn, held the canonical
conversation, and decided when they'd agreed.

**Why:** working through that design conversationally surfaced that the
external driver was a central coordinator wearing a disguise - the exact
thing D4b already named for the world channel, just recreated on the
negotiation channel instead. If these were real robots, nothing would play
that role; each robot has to decide its own turn and its own agreement
independently. The fix that makes independent polling safe with zero
coordination machinery: since a message gets appended to the shared history
on every turn, `len(history)` at any moment tells both robots whose turn is
next (even → A, odd → B) - no locks, no race conditions, nothing to
coordinate, because both robots compute the same number from the same shared
state.

**One small, honest exception to "zero diff to negotiation.py":** each
robot's loop needs to call the agreement check directly, since there's no
external driver left to alternate turns and call it centrally. That function
existed as `_check_agreement` - a leading-underscore "internal" name, at the
time only called inside `negotiate()` and imported directly by one test.
Once real runtime code (`agent.py`) depends on it too, the underscore was
actively misleading, so it was renamed to `check_agreement`. One-line rename
plus its one call site and one test import; behavior unchanged.

**Consequence:** `RemotePolicy` and the passive `agent_server.py` design
never shipped - `agent.py` calls `me.policy.respond(...)` directly, the same
as every in-process script. `negotiate()` itself is untouched and still
drives every in-process script (`run.py`, `eval.py`, `simulate.py`,
`world_eval.py`) unchanged; the networked case reuses its smaller building
blocks (`check_agreement`) directly instead of being driven by it.

**Would change our mind:** if a future phase needs a real handshake before
negotiation starts (agreeing on which scenario, confirming both sides are
up) - that's explicitly deferred to Phase 5's A2A agent cards, not something
to bolt onto this dumb board.

---

## D15 — Phase 5: real A2A, staged (core swap, then streaming, then webhooks)

**Decided:** `comms_server.py` is deleted, not adapted - A2A has no
message-board concept, so there's no longer a third process at all. Each
robot is its own process (`agent.py`) that's *either* a pure A2A client
(`--side a`, the initiator) *or* a pure A2A server (`--side b`, the
responder, hosting `agent_executor.NegotiationExecutor`) for a given
negotiation. One negotiation is one A2A task; the initiator convention
reuses Phase 4's `--side` split unchanged - side `a` always dials, exactly
like `len(history) % 2 == 0` already meant "A goes first." Built in three
stages, matching how Phase 4 was actually built (incrementally, not all at
once): plain request/response first, verified against `run.py`'s baseline;
then streaming; then webhooks - all three ended up built this phase.

**Rejected:** the original Phase 4 alternative of routing negotiation
through a relay-like third process - already rejected once for the
homemade board (D14) and doesn't reappear here; A2A's peer-to-peer model
makes that mistake structurally unavailable this time; there's no shared
process to accidentally lean on.

**A real finding that changed the design mid-build:** research into
`a2a-sdk`'s actual API (v1.1.2, protobuf-based - `a2a.types.a2a_pb2`, not
the plain-dataclass shape some older blog posts show) turned out
insufficient on its own, the same way reading `negotiation.py` alone
wouldn't have caught D11's `REJECT` bug. A live probe server/client caught
something the source didn't make obvious: a task's own pending
`status.message` (this robot's last reply) and the newest incoming
`context.message` are **not** folded into `task.history` until the
*following* call - only the framework's internal bookkeeping does that,
one call late. `agent_executor.history_from_context()` reconstructs the
true chronological history by hand every call
(`task.history + [status.message if present] + [context.message]`) rather
than trusting `task.history` alone. Confirmed with three live calls in a
row, watching the real object state change each time - not inferred from
documentation.

**Streaming:** the responder publishes one `TASK_STATE_WORKING` heartbeat
(`updater.start_work()`) before calling the (possibly slow) policy, so the
initiator can distinguish "peer is thinking" from "peer is down" - both
looked identical under Phase 4's plain request/response. Deliberately
narrow: this streams task *lifecycle* events, not the LLM's partial
output token-by-token - a coarse liveness pulse, not a content-streaming
feature. `should_respond()` was split out of what used to be
`agent_executor.decide()` specifically so the executor can publish that
heartbeat at the exact point between "confirmed there's a turn to take"
and "the policy call that takes it."

**Webhooks:** the initiator (`--side a --webhook`) registers a callback
URL and sends with `configuration.return_immediately=True`, gets back only
the task's creation ack, and does not hold the connection open - it awaits
its own tiny inbound receiver instead. This is the one piece that makes
Robot A a server too, not just a client, which is a real architectural
cost the plan flagged before building it (see the "webhook scope"
check-in). The responder (`run_responder`) is unconditionally wired with a
`PushNotificationConfigStore`/`PushNotificationSender` - dormant unless a
caller actually registers a config, so `run_initiator` (no `--webhook`)
is unaffected.

**A second live-caught bug, this time in webhook mode specifically:** the
very first attempt sent a duplicate message into an already-completed
task. Cause: the task-creation event that already came back synchronously
as the `return_immediately` ack *also* gets pushed to the webhook again
(state `SUBMITTED`), ahead of the real answer. The initiator's callback
loop was only filtering out `WORKING`, so it treated that duplicate
`SUBMITTED` push as if it were the final answer, decided nothing had been
resolved, and sent another message - which the server correctly rejected
("Task is already completed"). Fixed by filtering out both `SUBMITTED`
and `WORKING` as "not yet an answer," not just `WORKING`. Caught by
actually running two live processes against each other, not by reasoning
about the SDK's event model in the abstract - same discipline as the
`history_from_context` finding above, applied a second time in the same
phase.

**New dependencies:** `a2a-sdk` pulls in a meaningfully heavier chain than
anything else in this project - `protobuf`, `google-api-core`,
`google-auth`, `cryptography` - plus `sse-starlette`, an undeclared
transitive dependency its JSON-RPC routes need directly (import fails
without it; added to `requirements.txt` explicitly rather than left
implicit). This is a real, deliberate departure from D7/"boring stdlib
code, new dependencies need a reason" - the reason here is simply that
this is what adopting the *real* protocol costs; it was flagged, not
absorbed silently.

**Would change our mind:** if a genuinely long-running policy shows up
(the thing webhooks are actually for), worth re-testing this exact path
against it rather than trusting today's fast-policy-only verification
generalizes; today's negotiations all resolve in well under a second, so
the webhook path has only been proven mechanically correct, not proven
under real long-wait conditions.

---

## D16 — A networked robot process only ever loads its own half of a scenario

**Decided:** `scenarios.py` gets one new accessor, `for_side(scenario_id,
side) -> (situation, urgency)`. `agent.py`'s `build_robots()` calls it
instead of taking a `Scenario` object at all - `main()` no longer does
`BY_ID[args.scenario]` and never holds a reference to the full paired
scenario. Only `run.py`/`eval.py`/`simulate.py`/`world_eval.py` still load
the full `Scenario` (both halves + `should_go_first`), because they
legitimately need it - they're the omniscient single-process harness that
scores correctness, not a robot.

**Rejected:** leaving `agent.py` to keep loading the full `Scenario` and
trusting `build_robots()` to only read its own half - which is exactly
what it already did, correctly, before this decision. The gap wasn't a
bug; it was the same kind of discipline-not-structure gap the `other =
Robot(name, "", 0)` placeholder (Phase 4 v1's "Correction 1") already
closed for the *other robot's* `Robot` object. This closes the matching
gap one level up, for where `me`'s own data comes from.

**Why:** raised directly by the user while reviewing Phase 5 - since the
robots are already separate OS processes (Phase 4), a scenario should
define what *one* robot sees, not a pair, the same way `PLAN.md` §4.1
already frames a robot's situation as private. Before this, nothing
*read* the other robot's secret in `agent.py`, but the full pair sat in
that process's own working memory (`scenario.a_situation` and
`scenario.b_situation` both reachable from one local variable) for the
whole run - a future change (a stray debug print, a refactor that grabs
the wrong field) could leak it with no test catching it. Same reasoning
as D12's "the reactive layer's guarantee needs to hold even if the
executive layer is wrong, later, for reasons nobody anticipated," applied
to secrecy instead of collision safety.

**An honest limit on how far this goes:** `scenarios.py` itself is one
shared, statically-defined module (`SCENARIOS` embeds both halves of
every scenario in source) that both `--side a` and `--side b` processes
import - so the *data* for both halves technically still loads into both
processes' memory the moment `agent.py` does `from scenarios import
...`, same as `negotiation.py`'s `LLMPolicy` class being importable by
both sides. What changed is that neither process's own *working state*
(`me`, `other`, or any local variable in `agent.py`/`agent_executor.py`)
ever holds a reference to the other side's values anymore - confirmed by
grepping both files for `scenario.` and `BY_ID[` after the change: zero
hits outside `scenarios.py` itself. This is "shared code, not shared
state," the same distinction already established for why `negotiation.py`
being one file both processes import is fine. Going further - genuinely
splitting the source data per side, e.g. two files or a per-side lookup
service - would be the next step if this project ever needed to defend
against something reading the *module*, not just something reading
`agent.py`'s own variables; not needed for a learning project with no
adversarial attacker model.

**Would change our mind:** if Phase 8+ (three or more robots, real
services) ever puts scenario data behind something other robots could
plausibly query (a shared config service, a mounted file) - at that point
"shared code" stops being an accurate description and the harder
per-side-file split from the paragraph above would be worth doing for
real.

---

## D17 — Phase 6: the world as a real MCP server, and who executes vs. who validates

**Decided:** `world_server.py` is a new MCP server exposing three tools -
`get_map()`, `get_observation(side)`, `propose_action(side, action)`. It
holds the only copy of ground-truth position (`WorldState`, reused
directly from `world.py`, unchanged). `agent.py` gets a `--world-url`
flag; when given one, it also becomes an MCP client and runs a movement
loop alongside its existing A2A negotiation role. Negotiation stays
exactly as Phase 5 built it (`--side a` is still the fixed initiator, per
the plan's explicit staging) - the only thing that changed this phase is
*when* it fires: gated on a robot's own `get_observation()` saying it's
at the boundary and can sense the other, matching Phase 3's original
physical logic (D12), now driven by a real MCP call instead of an
in-process simulator check. Arriving at the boundary alone still claims
priority for free, zero negotiation, exactly as before.

`world.py`/`simulate.py` are **completely untouched** - not "split," the
original plan's wording. `simulate.py` still needs the full
`executive_decide`/`_resolve_priority` chain for its own single-process
path, so nothing could be deleted from `world.py` without breaking it.
`world_server.py` instead directly reuses `WorldState`/`RobotState`/
`reactive_filter`/`apply` - the genuinely `Robot`-agnostic, safety-critical
pieces - rather than moving or reimplementing anything.

**What actually shipped is not what was first designed, in two ways -
both caught only by building and testing, not by planning ahead:**

**1. `propose_action`, not `propose_move`, and no background clock at
all.** The original design (matching the approved plan) had the world
run its own independent tick loop, applying whatever each robot had most
recently proposed on a fixed schedule, with `propose_move()` reporting
`last_applied` - the outcome of the *previous* tick, one full round
behind whatever was just proposed. Working through this live surfaced a
real problem: `last_applied` is only meaningful if a robot's own polling
rate happens to line up with the server's independent tick rate, which
nothing guarantees - poll faster and you see stale repeats, poll slower
and you silently miss intermediate ticks. Worse, it also drifted from
what a real robot actually is: a real robot commands its own motors and
knows immediately whether it moved, it doesn't get told after the fact
by an external clock.

The resolution, reached collaboratively rather than designed upfront: the
robot still decides and commits its own action, but the world's
authority narrows to exactly the one thing no single robot can safely
decide alone - whether it's safe to enter the shared corridor zone right
now. `propose_action(side, action)` resolves **synchronously**, checked
against `reactive_filter` and the world's *current* state (the other
robot's action held fixed as `"wait"`, since only one robot transitions
per call - there's no such thing as "simultaneous" once actions commit
one at a time, so the both-enter-at-once case Phase 3 worried about
structurally can't arise here), and returns the real, authoritative
outcome immediately: `accepted` and `actual_position`. No independent
clock, no staleness, no lag - and less code than the version with the
background tick loop, not more.

**Why position still lives in the world, not the robot:** raised
directly in the same conversation - a position number only means
anything relative to a shared coordinate system (the corridor, the
boundaries, the zone) that the *world* defines, not something a robot can
have an opinion about in isolation. So the robot still asks
`get_observation()` for the current truth and still treats its own
belief as provisional until confirmed - it just now also *drives* that
truth via its own committed actions, instead of passively receiving
whatever an independent clock decided.

**The real-world analogy that resolved the disagreement:** the world
retaining veto power over zone entry isn't "the world doing the robot's
job" - it's the same thing rail interlocking and air traffic control do
for a genuinely shared, contested resource. A train drives itself; it
still doesn't enter a single-track section without permission from a
signaling authority that tracks occupancy of *that specific resource*.
Nobody considers that the signal box driving the train.

**2. `NegotiationExecutor` needed a callback it didn't have.** Under the
fixed-initiator convention, `--side b` never calls out - its own
negotiated outcome only ever becomes known *inside*
`NegotiationExecutor.execute()`, reacting to whatever `--side a` sends
whenever it gets around to it. `--side b`'s own movement loop has no way
to `await` that. Added `on_resolved`, an optional callback invoked at
both points `execute()` learns `check_agreement()` is decided - the one
way the movement loop learns priority was ever set at all.

**Consequence:** `run_responder` (the plain, `--side b`-only path) is
unchanged; a new `run_responder_async` (built on the same
`uvicorn.Server(...).serve()`-as-a-background-task pattern
`run_initiator_webhook` already established in Phase 5) runs the A2A
server concurrently with the movement loop when `--world-url` is given.
`run_initiator`/`run_initiator_webhook` now return the negotiated outcome
instead of only printing it, so `run_initiator_with_world` can use it
directly.

**Verified end to end:** `world_server.py` + two `--world-url` `agent.py`
processes, deterministic policies, `dying_battery_vs_fragile_cargo` -
final positions (A=8, B=1) and outcome (agreed on Robot A) matched
`simulate.py`'s in-process baseline exactly. Collision safety re-verified
live across the real process boundary under the new synchronous design,
same as the first version.

**Explicitly deferred, unchanged from the plan:** boundary-triggered
*dynamic* initiation (either robot can become the initiator, with jitter
+ a name tiebreak for the race case) and the visualizer rebuild - both
still their own follow-on plans.

**Would change our mind:** if a future phase needs multiple robots
transitioning in true lockstep (a synchronized global tick actually
mattering, not just each robot's own local decision) - that would justify
reintroducing something like the independent clock this decision removed,
but nothing in this project currently needs that.

---

## D18 — Boundary-triggered dynamic initiation: every robot can dial, races resolved by jitter + tiebreak + cancellation

**Decided:** `agent.py`'s `--side a`/`--side b` split no longer means
"client-only" vs "server-only." `run_initiator_with_world`/
`run_responder_with_world` collapse into one `run_robot()` - every robot
always runs its own A2A server (`run_responder_async`) *and* its own
movement loop, and is capable of dialing the peer the instant its own
`get_observation()` says it's at the boundary and can sense the other.
`--side` now only picks which half of the scenario to load (D16) and
which robot name to use.

**Mechanism:** the dial is jittered - a one-time random deadline
(`now + random.uniform(0, 1.0)`) computed the moment the trigger
condition first becomes true, checked against `time.monotonic()` each
loop iteration rather than slept inline, so the loop keeps
polling/proposing the whole time. When the deadline passes, the dial
runs as a cancellable `asyncio.Task`, not an inline `await`.
`NegotiationExecutor` gained `on_task_started` (fires once, with the
peer's name, the instant a brand-new incoming task arrives - earlier
than the existing `on_resolved`, which only fires once a negotiation
*concludes*). The race case (both robots dial before either sees the
other's incoming call) is resolved with full cancellation: whichever
robot loses a fixed, deterministic tiebreak (lexicographically smaller
name wins - "Robot A" always wins a tie) cancels its own outbound
`asyncio.Task` the instant it learns of the incoming one - before
wasting a policy call, not after. Verified live, repeatedly: the same
scenario correctly resolves to the same ground-truth-correct outcome
regardless of which side actually ends up dialing, and a genuinely
forced race shows the exact `"race - {peer} called in while I was
dialing - deferring"` cancellation line, with the negotiation still
concluding correctly through the surviving task.

**Two real bugs, both found only by running this live - the plan
anticipated the race but not either of these:**

1. **A dial that fails (e.g. the peer's server socket isn't bound yet)
   crashed the entire robot process.** `dial_task.result()` re-raises
   whatever exception the task ended with; nothing caught it. Fixed by
   checking `dial_task.exception()` first - a failed dial now logs a
   warning and resets the jitter deadline so a fresh attempt gets
   scheduled on a later loop iteration, instead of taking the whole
   process down. A real robot doesn't die because it called a peer a
   moment too early.

2. **A robot could start a *second*, redundant dial while it was
   already the responder on an active incoming negotiation.**
   `on_task_started` only cancels an outbound dial that's *already in
   flight* - it does nothing to stop a *new* one from starting later,
   because `priority_holder` (what the movement loop checks before
   deciding to dial) only gets set once a negotiation *concludes*
   (`on_resolved`), not when one *starts*. With a slow LLM call widening
   the window, the responding side's own movement loop reached its
   jitter deadline mid-negotiation and dialed the peer it was already
   talking to - two simultaneous negotiations between the same two
   robots. Fixed with a third piece of state, `incoming_active`, set
   `True` in `on_task_started` and cleared in `on_resolved` - the
   movement loop now checks it before ever starting a new dial, not just
   before letting an in-flight one continue. This is the more important
   of the two fixes: the in-flight-cancellation mechanism the plan
   designed for handles simultaneous starts, but says nothing about
   preventing a start *during* an already-active negotiation - a gap the
   plan's design section didn't anticipate because it was reasoning about
   the moment two dials begin, not the whole duration one stays open.

**A pre-existing limitation, unchanged:** `LLMPolicy.respond()` is still
a synchronous call inside an `async def` (D15) - a slow LLM call blocks
the entire process, including its own race-detection and world-polling,
for the duration of that call. Not fixed here, as planned.

**Would change our mind:** if `JITTER_SECONDS=1.0` turns out to still
produce races often enough in practice to be annoying (rather than the
rare, correctly-handled case observed in testing) - worth widening it,
or reconsidering whether the tiebreak should be primary rather than a
fallback, if evidence suggests otherwise.

---

## D19 — The network visualizer reconstructs priority after the fact; the template didn't change at all

**Decided:** `visualize_network.py` is a new, small post-hoc tool -
`visualize_template.html` is reused completely unchanged, same split
Phase 3 already used (D13): only the data-builder is new. It fetches a
completed episode's movement log from `world_server.py` over MCP
(`get_map` + the new `get_log` tool) and reads
`experiments/results/negotiation_trace.json` if a negotiation happened -
written by whichever robot's `agent.py` process ends up holding the full
transcript when a negotiation concludes (`write_negotiation_trace()`,
called from both the dial-success path and `on_resolved`, since either
side can now be the one who learns the outcome first - D18 made this
genuinely either-or, not fixed to one side).

**Why priority has to be reconstructed, not read:** `world_server.py`'s
log has no concept of priority at all, by design (D17) - the world only
ever sees move/wait proposals, never who negotiated what or why. So
`visualize_network.py` infers it after the fact from the simplest signal
the log actually contains: a robot "has priority" from the first log
entry where it successfully moves off its own boundary position -
whether that was negotiated or claimed for free, that's the moment "who
goes first" became real, and it's derivable from `a_position`/`b_position`
alone with no reference to *why* the world allowed it. That same index
also anchors where the negotiation dialogue gets attached in the replay,
if one happened - not a claim of matching some real "tick" the way Phase
3's single-process design had one, since the world channel and the
negotiation channel are now genuinely asynchronous and don't share a
clock.

**Rejected:** trying to correlate world-log timing with A2A message
timing precisely (e.g. threading a shared timestamp or tick counter
through both channels). Not worth the complexity for a debugging
visualization - D4b already establishes that the world and negotiation
channels are deliberately separate and shouldn't be coupled; adding
timing correlation between them would be exactly the kind of coupling
that principle warns against, just for cosmetic replay accuracy.

**Verified:** a real live episode's generated `episode_data` was
inspected directly (not just assumed correct from the code) - the log
correctly shows both robots holding at their boundaries while the A2A
negotiation happens off in its own channel (multiple consecutive
`wait`/`wait` rows with `priority: null`), priority flips to the winner
at exactly the row where that robot's position first moves past its
boundary, `negotiation.tick` lands on that same row, and the messages
match the real negotiation transcript. The "no negotiation, arrived
alone" code path was verified separately with hand-built data, since
(same as Phase 3) the project's fixed grid geometry and `SENSOR_RANGE`
mean every real scenario run always senses a conflict by the boundary -
that path was never naturally reachable through a live run in Phase 3
either.

**Would change our mind:** if a future phase makes the grid/sensor
geometry configurable enough that "arrived alone" becomes a real,
reachable outcome - worth adding a live end-to-end check for it then,
not just the hand-built one.

---

## D20 — "Tick" was a lie in the network visualizer; real timestamps replace it

**Decided:** `world_server.py`'s log entries now carry a real
`time.time()` timestamp per `propose_action` call, returned by
`get_log()`. `visualize_network.py` drops the word "tick" entirely -
`entry.tick` becomes `entry.step` (a plain sequence index, no claim of
synchronization) and each row carries `elapsed_ms`, the real time since
the previous entry. `visualize_template.html` - genuinely shared between
Phase 3's `visualize.py` and this - detects which shape it was given
(`log[0].step !== undefined`) and behaves accordingly: Phase 3 data still
gets "tick" labels and the fixed `TICK_DELAY_MS` pacing unchanged; step
data gets "step" labels and per-row delay from real `elapsed_ms`, clamped
to [300ms, 3000ms] so a near-instant real gap stays visible and a long
one doesn't force a multi-second wait in autoplay.

**Why:** raised directly by the user reviewing the visualizer - Phase
3's "tick" was real (one process, one `step()` call, both robots decided
and applied together). Once the world stopped having a shared clock
(D17), each log entry became one robot's independent, serialized commit,
arriving whenever its own async loop happened to call in. Labeling these
"tick 5," "tick 6" as if they were synchronized steps was quietly
reintroducing a false synchronization the system doesn't have - it hid
two real distortions: genuinely near-simultaneous events (two robots
proposing within milliseconds of each other) got serialized into
strictly-ordered ticks as if one meaningfully preceded the other, and a
genuine multi-second gap (e.g., a slow LLM negotiation) was invisible,
since consecutive step numbers always increment by 1 regardless of how
much real time passed.

**Rejected:** leaving the label as "tick" and treating it as a cosmetic
issue. It wasn't cosmetic - the whole point of the visualizer (D13) is to
build an accurate mental model of what actually happened, and a fake
synchronized clock actively works against that for the one thing this
phase's architecture most needs explaining: that the world and
negotiation channels are genuinely asynchronous now.

**Scope boundary, deliberate:** only movement/step pacing uses real
elapsed time. Negotiation dialogue reveal still uses the fixed
`MESSAGE_DELAY_MS`/`DECISION_DELAY_MS` - those are about giving a human
reader time to read each line, not about reproducing real LLM latency
(nobody wants to actually sit through a real multi-second API call while
watching a replay), and the negotiation trace file doesn't carry
per-message timestamps to do that with anyway.

**Verified:** live episode inspection shows realistic elapsed_ms values
(mostly ~0-215ms, matching the movement loop's ~0.2s poll interval) at
every step including around the negotiation step, and confirmed Phase
3's `visualize.py` output is byte-for-byte the same shape as before (no
`step`/`elapsed_ms` fields at all) - the template's shape-detection
correctly falls back to the old tick-based behavior for it, unchanged.

**Would change our mind:** if the negotiation trace ever gains real
per-message timestamps (e.g., to show LLM thinking time in the replay
the same way the live streaming heartbeat does) - worth extending the
dialogue reveal to use real elapsed time too, at that point.
