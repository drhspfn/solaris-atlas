import assert from 'node:assert/strict';
import test from 'node:test';

import { cutsceneSlots } from '../src/components/story/cutscenePlacement.ts';

test('explicit transition position beats lexical flow name and does not repeat on a later page', () => {
  const lines = [{ id: 'a', flow_state: 'z', story_position: [1, 0, 0] },
    { id: 'b', flow_state: 'a', story_position: [3, 0, 0] }];
  const event = { kind: 'cutscene', reference: 'cutscene:M0355_Loading', flow_state: '',
    action_index: 0, story_position: [2, 100001, 2] };
  assert.deepEqual(cutsceneSlots(lines, [event]).get(1), [event]);
  assert.equal(cutsceneSlots(lines.slice(1), [event], true).size, 0);
});

test('mapped scene appears before its caption lines; unmapped scene stays separate', () => {
  const lines = [{ id: 'a', flow_state: 'earlier' }, { id: 'b', flow_state: 'caption' }];
  const mapped = { kind: 'cutscene', reference: 'cutscene:M0366', flow_state: 'movie',
    action_index: 1, context_inferred: true, story_order_mapped: true, transcript_states: ['caption'] };
  const unknown = { ...mapped, reference: 'cutscene:unknown', story_order_mapped: false };
  const result = cutsceneSlots(lines, [mapped, unknown]);
  assert.deepEqual(result.get(1), [mapped]);
  assert.deepEqual(result.get(2), [unknown]);
});
