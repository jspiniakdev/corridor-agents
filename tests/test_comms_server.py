"""Tests for comms_server.py. Uses FastAPI's TestClient, which exercises the
real app in-process with no real socket - fast, deterministic, zero API
calls, same as every other test in this project.
"""

import sys

sys.path.insert(0, ".")

from fastapi.testclient import TestClient  # noqa: E402

import comms_server  # noqa: E402

client = TestClient(comms_server.app)


def reset_messages():
    comms_server.messages.clear()


def test_starts_empty():
    reset_messages()
    response = client.get("/messages")
    assert response.status_code == 200
    assert response.json() == []


def test_posted_message_can_be_read_back():
    reset_messages()
    message = {"speaker": "Robot A", "intent": "propose", "goes_first": "Robot A", "text": "I go first."}
    client.post("/messages", json=message)

    response = client.get("/messages")
    assert response.json() == [message]


def test_messages_accumulate_in_order():
    reset_messages()
    first = {"speaker": "Robot A", "intent": "propose", "goes_first": "Robot A", "text": "first"}
    second = {"speaker": "Robot B", "intent": "accept", "goes_first": "Robot A", "text": "second"}
    client.post("/messages", json=first)
    client.post("/messages", json=second)

    assert client.get("/messages").json() == [first, second]


def test_server_has_no_negotiation_awareness_and_accepts_anything():
    """The whole point of this server: it must never reject a message for
    being out of turn order, from the wrong speaker twice in a row, or
    anything else about negotiation content - that logic belongs entirely
    to each robot, not here."""
    reset_messages()
    same_speaker_twice = {"speaker": "Robot A", "intent": "propose", "goes_first": "Robot A", "text": "again"}

    first_response = client.post("/messages", json=same_speaker_twice)
    second_response = client.post("/messages", json=same_speaker_twice)

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert len(client.get("/messages").json()) == 2
