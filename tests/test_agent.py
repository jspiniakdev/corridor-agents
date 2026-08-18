"""Tests for agent.py's decide() - the pure per-turn decision logic. Zero
API calls, zero network - only deterministic policies and hand-built
history lists, same style as test_negotiation.py. The while-loop/sleep/HTTP
glue in main()/run() is thin and manually verified instead, same as every
other script's main() in this project.
"""

import sys

sys.path.insert(0, ".")
sys.path.insert(0, "src")

from negotiation import Intent, Message, Robot, Stubborn  # noqa: E402
from agent import decide  # noqa: E402


def make_robot(name):
    return Robot(name, "", 0, Stubborn())


def a_turn(count):
    return count % 2 == 0


def test_returns_none_when_it_is_not_this_robots_turn():
    me = make_robot("Robot A")
    other = make_robot("Robot B")
    history = [Message("Robot A", Intent.PROPOSE, "Robot A", "first")]  # len=1 -> B's turn, not A's
    assert decide(me, other, history, a_turn, max_turns=6) is None


def test_returns_a_message_when_it_is_this_robots_turn():
    me = make_robot("Robot A")
    other = make_robot("Robot B")
    message = decide(me, other, [], a_turn, max_turns=6)  # len=0 -> A's turn
    assert message is not None
    assert message.speaker == "Robot A"


def test_returns_none_once_agreement_is_reached():
    me = make_robot("Robot A")
    other = make_robot("Robot B")
    history = [
        Message("Robot B", Intent.PROPOSE, "Robot B"),
        Message("Robot A", Intent.ACCEPT, "Robot B"),
    ]
    # len(history)=2 -> a_turn(2) is True, but agreement is already decided
    assert decide(me, other, history, a_turn, max_turns=6) is None


def test_returns_none_once_max_turns_reached():
    me = make_robot("Robot A")
    other = make_robot("Robot B")
    history = [Message("Robot A", Intent.PROPOSE, "Robot A")] * 6
    assert decide(me, other, history, a_turn, max_turns=6) is None
