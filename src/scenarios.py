"""Corridor scenarios.

Each scenario gives the two robots asymmetric private situations plus a
ground-truth `urgency` (0-10) that NO policy ever sees. Urgency exists purely so
Phase 2 can ask the interesting question: did the genuinely more urgent robot
actually go first?

Keeping urgency out of the prompt is deliberate. If a policy could see it, the
negotiation would be trivial and we'd be measuring nothing.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Scenario:
    id: str
    a_situation: str
    a_urgency: int
    b_situation: str
    b_urgency: int

    @property
    def should_go_first(self) -> str | None:
        """Ground truth, or None when it's genuinely a toss-up."""
        if self.a_urgency == self.b_urgency:
            return None
        return "Robot A" if self.a_urgency > self.b_urgency else "Robot B"


SCENARIOS = [
    Scenario(
        id="dying_battery_vs_fragile_cargo",
        a_situation=(
            "Your battery is at 8%. The charging dock is beyond the corridor. If you wait "
            "more than about a minute you will shut down where you stand and block the "
            "corridor completely."
        ),
        a_urgency=9,
        b_situation=(
            "You are carrying a tray of unsecured glass vials. Starting and stopping risks "
            "shattering them, so you would prefer to move once, continuously. Nothing "
            "terrible happens if you wait - it is merely wasteful."
        ),
        b_urgency=3,
    ),
    Scenario(
        id="routine_vs_medical",
        a_situation="You are returning an empty pallet to storage. There is no deadline at all.",
        a_urgency=1,
        b_situation=(
            "You are carrying blood products to an operating theatre. A surgery is waiting "
            "on your arrival."
        ),
        b_urgency=10,
    ),
    Scenario(
        id="both_urgent",
        a_situation=(
            "Your coolant system is failing. If you do not reach the maintenance bay beyond "
            "the corridor within two minutes you risk permanent damage."
        ),
        a_urgency=8,
        b_situation=(
            "You are carrying a thermal payload that spoils in under two minutes. Beyond the "
            "corridor is the only freezer on this floor."
        ),
        b_urgency=8,
    ),
    Scenario(
        id="both_trivial",
        a_situation="You are on a routine patrol loop. Nothing depends on your timing.",
        a_urgency=2,
        b_situation="You are heading to your idle parking spot at end of shift. No deadline.",
        b_urgency=2,
    ),
    Scenario(
        id="quiet_emergency",
        a_situation=(
            "You have detected a small coolant leak from your own chassis. It is not yet "
            "visible to others. Every minute of delay spreads it further across the floor."
        ),
        a_urgency=7,
        b_situation=(
            "You are moderately behind schedule on a delivery run. Being late is embarrassing "
            "but harmless."
        ),
        b_urgency=4,
    ),
]

BY_ID = {s.id: s for s in SCENARIOS}


def for_side(scenario_id: str, side: str) -> tuple[str, int]:
    """The one thing a networked robot process (agent.py) is allowed to
    load: its own situation and urgency, nothing else about the paired
    scenario. The full Scenario - both halves plus should_go_first -
    stays inside the in-process tools (run.py/eval.py/simulate.py/
    world_eval.py), which legitimately need it to score correctness.
    Real robots don't get a pointer to the whole scenario file; they get
    their own briefing."""
    scenario = BY_ID[scenario_id]
    if side == "a":
        return scenario.a_situation, scenario.a_urgency
    return scenario.b_situation, scenario.b_urgency
