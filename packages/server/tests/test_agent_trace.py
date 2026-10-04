import pytest
from pydantic import BaseModel, ValidationError

from wuwa_story.agents.trace import legacy_trace, preview, signature, validation_message


def test_validation_keeps_field_paths_without_rejected_input():
    class Result(BaseModel):
        count: int
    with pytest.raises(ValidationError) as failure:
        Result(count="sensitive-rejected-input")
    message = validation_message(failure.value)
    assert 'count' in message and 'int_parsing' in message
    assert 'sensitive-rejected-input' not in message


def test_previews_bounded_and_credentials_redacted():
    assert 'credential-value' not in preview({'api_key': 'credential-value'})
    assert '[truncated]' in preview('x' * 5000)
    assert signature('read', {'b': 2, 'a': 1}) == signature('read', {'a': 1, 'b': 2})
    assert signature('read', {'offset': 0}) != signature('read', {'offset': 20})


def test_legacy_only_exposes_visible_messages_and_calls():
    trace = legacy_trace({'output': [
        {'type': 'reasoning', 'encrypted_content': 'private'},
        {'type': 'message', 'content': [{'type': 'output_text', 'text': 'Visible'}]},
        {'type': 'function_call', 'name': 'read_quest', 'arguments': '{"offset":20}'},
    ]})
    assert trace['recorded'] is False
    assert trace['text'] == 'Visible'
    assert trace['tools'][0]['status'] == 'unknown'
    assert 'private' not in str(trace)
    assert legacy_trace({'candidates': [{'content': {'parts': [
        {'thought': True, 'text': 'hidden'}, {'text': 'public'}]}}]})['text'] == 'public'
