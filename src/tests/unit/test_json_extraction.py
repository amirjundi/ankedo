"""Getting JSON out of a model that will not only send JSON.

A paid endpoint honours response_format and returns a bare object. Free and open
models routinely do not: a sentence, then a ```json fence, then sometimes a sign-off.
Pydantic rejected all of it and the chat answered "response did not match
ChatDecision" — which reads as the agent being broken rather than the model being
chatty. Reproduced against a stand-in behaving like the operator's proxy, where it
made the chat completely silent.
"""
from __future__ import annotations

import json

import pytest
from pydantic import BaseModel

from src.classifiers.llm_client import _extract_json

PAYLOAD = {"action": "reply", "message": "النظام يعمل."}
BARE = json.dumps(PAYLOAD, ensure_ascii=False)


class Reply(BaseModel):
    action: str
    message: str


@pytest.mark.parametrize(
    "raw",
    [
        BARE,
        f"  {BARE}  ",
        f"```json\n{BARE}\n```",
        f"```\n{BARE}\n```",
        f"Sure, here is the JSON you asked for:\n\n```json\n{BARE}\n```",
        f"Here you go: {BARE}",
        f"{BARE}\n\nLet me know if you need anything else!",
        f"Certainly.\n```json\n{BARE}\n```\nHope that helps.",
    ],
)
def test_the_object_is_recovered_however_it_is_wrapped(raw):
    assert Reply.model_validate_json(_extract_json(raw)) == Reply(**PAYLOAD)


def test_a_brace_inside_a_string_does_not_end_the_object():
    """Rationales in this domain quote text, and quoted text contains braces."""
    payload = json.dumps({"action": "reply", "message": 'he wrote "{" then left'})

    recovered = _extract_json(f"Here:\n```json\n{payload}\n```")

    assert json.loads(recovered)["message"] == 'he wrote "{" then left'


def test_an_escaped_quote_does_not_end_the_string():
    payload = json.dumps({"action": "reply", "message": 'say \\"hello\\" twice'})

    assert json.loads(_extract_json(f"text {payload} more"))["action"] == "reply"


def test_nested_objects_survive():
    payload = json.dumps({"action": "x", "message": "y", "trace": {"inner": {"deep": 1}}})

    assert json.loads(_extract_json(f"```json\n{payload}\n```"))["trace"]["inner"]["deep"] == 1


def test_text_with_no_object_is_returned_unchanged():
    """So the schema error names what actually came back, not a mangled slice."""
    assert _extract_json("I'm sorry, I can't help with that.") == "I'm sorry, I can't help with that."


def test_an_unterminated_object_is_left_alone():
    """A truncated response must fail loudly, not be silently repaired."""
    truncated = '{"action": "reply", "message": "cut off'

    with pytest.raises(Exception):
        Reply.model_validate_json(_extract_json(f"here: {truncated}"))


def test_the_error_shows_what_the_model_actually_said():
    """"did not match the schema" with no payload leaves nothing to act on."""
    from src.classifiers.llm_client import _OpenAIBackend

    # The message is built in complete(); assert the contract it relies on instead:
    # unparseable text comes back intact for the error to quote.
    assert "I refuse" in _extract_json("I refuse")
