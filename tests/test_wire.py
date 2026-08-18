"""Tests for src/wire.py's Message <-> dict conversion. Pure, no network,
zero API calls.
"""

import sys

sys.path.insert(0, "src")

from negotiation import Intent, Message  # noqa: E402
from wire import (  # noqa: E402
    history_from_list,
    history_to_list,
    message_from_dict,
    message_to_dict,
)


def test_message_round_trips_through_a_dict():
    original = Message("Robot A", Intent.PROPOSE, "Robot A", "Requesting to go first.")
    rebuilt = message_from_dict(message_to_dict(original))
    assert rebuilt == original


def test_message_with_no_goes_first_round_trips():
    original = Message("Robot A", Intent.INFORM, None, "")
    rebuilt = message_from_dict(message_to_dict(original))
    assert rebuilt == original


def test_message_to_dict_uses_the_plain_intent_string_not_the_enum():
    data = message_to_dict(Message("Robot A", Intent.ACCEPT, "Robot B", "Fine."))
    assert data["intent"] == "accept"
    assert isinstance(data["intent"], str)


def test_history_round_trips_as_a_list():
    original = [
        Message("Robot A", Intent.PROPOSE, "Robot A", "I go first."),
        Message("Robot B", Intent.ACCEPT, "Robot A", "Fine."),
    ]
    rebuilt = history_from_list(history_to_list(original))
    assert rebuilt == original
