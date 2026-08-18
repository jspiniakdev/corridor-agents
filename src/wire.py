"""Phase 4: converting Message objects to and from plain dicts, so they can
travel as JSON over HTTP. Pure - no network import here at all, so this can
be tested (and reused) with nothing running.

Field names match Message exactly, mirroring how LLMPolicy already
serializes messages internally for the Anthropic API.
"""

from __future__ import annotations

from negotiation import Intent, Message


def message_to_dict(message: Message) -> dict:
    return {
        "speaker": message.speaker,
        "intent": message.intent.value,
        "goes_first": message.goes_first,
        "text": message.text,
    }


def message_from_dict(data: dict) -> Message:
    return Message(
        data["speaker"],
        Intent(data["intent"]),
        data.get("goes_first"),
        data.get("text", ""),
    )


def history_to_list(history: list[Message]) -> list[dict]:
    result = []
    for message in history:
        result.append(message_to_dict(message))
    return result


def history_from_list(data: list[dict]) -> list[Message]:
    result = []
    for item in data:
        result.append(message_from_dict(item))
    return result
