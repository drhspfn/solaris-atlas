"""Actual release21 choices without explicit branches must not acquire jumps."""

import json
from pathlib import Path

import pytest

from wuwa_story_worker.compiler.core import Writer
from wuwa_story_worker.compiler.flow import compile_flow

FIXTURE = json.loads((Path(__file__).parents[1] / 'fixtures/null_choice_release21.json').read_text(encoding='utf8'))


@pytest.mark.parametrize('case', FIXTURE['cases'], ids=lambda case: case['classification'])
def test_actual_choices_preserve_actions_and_authored_continuation_without_branch(tmp_path, case):
    for relative in ('BinData/flow/flow.json', 'BinData/speaker/speaker.json', 'BinData/cgVedio/videodata.json'):
        source = tmp_path / relative
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text('[]', encoding='utf8')
    path = tmp_path / 'BinData/flowState/flowstate.json'
    path.parent.mkdir(parents=True)
    # Retain original action/talk/option indices and therefore canonical IDs.
    actions = [{'Name': 'Wait', 'Params': {}}] * case['action_index'] + [case['action']]
    path.write_text(json.dumps([{'StateKey': case['state_key'], 'Id': 1,
                                'Actions': json.dumps(actions)}]), encoding='utf8')
    writer = Writer(tmp_path / 'out', '3.7.0')
    compile_flow(tmp_path, writer, {})
    writer.finish()
    choices = [json.loads(line) for line in (tmp_path / 'out/entities/player_choice.jsonl').read_text(encoding='utf8').splitlines()]
    choice, = [c for c in choices if c['id'] == case['choice_id']]
    option = case['action']['Params']['TalkItems'][case['talk_index']]['Options'][case['option_index']]
    assert choice['raw'] == option
    assert choice['source']['raw_path'].endswith(
        f".Params.TalkItems[{case['talk_index']}].Options[{case['option_index']}]")
    edges = [json.loads(line) for line in (tmp_path / 'out/graphs/global.jsonl').read_text(encoding='utf8').splitlines()]
    assert any(e['to'] == case['choice_id'] and e['type'] == 'presents_choice' for e in edges)
    assert not any(e['from'] == case['choice_id'] and e['type'] == 'choice_branch' for e in edges)
    nested = [json.loads(line) for line in (tmp_path / 'out/entities/nested_action.jsonl').read_text(encoding='utf8').splitlines()] if case['option_action_names'] else []
    owned = {e['to'] for e in edges if e['from'] == case['choice_id'] and e['type'] == 'contains_nested_action'}
    assert [a['name'] for a in nested if a['id'] in owned] == case['option_action_names']
    assert not any(e['from'] in owned and e['type'] == 'jumps_to_talk' for e in edges)
    if case['authored_continuation_talk_id'] is not None:
        items = case['action']['Params']['TalkItems']
        index, = [i for i, item in enumerate(items) if item['Id'] == case['authored_continuation_talk_id']]
        assert any(e['from'] == f"talk_item:{case['state_key']}:{case['action_index']}:{case['talk_index']}"
                   and e['to'] == f"talk_item:{case['state_key']}:{case['action_index']}:{index}"
                   and e['type'] == 'next_authored_talk' for e in edges)
    assert not any(d['code'] == 'unresolved_choice_target' for d in writer.diagnostics)
