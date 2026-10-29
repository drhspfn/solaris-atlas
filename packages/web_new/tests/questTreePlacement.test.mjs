import assert from 'node:assert/strict';
import test from 'node:test';

import { cutsceneSlots } from '../src/components/story/cutscenePlacement.ts';

test('mapped scene appears before its caption lines; unmapped scene stays separate', () => {
  const lines = [{ id: 'a', flow_state: 'earlier' }, { id: 'b', flow_state: 'caption' }];
  const mapped = { kind: 'cutscene', reference: 'cutscene:M0366', flow_state: 'movie',
    action_index: 1, context_inferred: true, story_order_mapped: true, transcript_states: ['caption'] };
  const unknown = { ...mapped, reference: 'cutscene:unknown', story_order_mapped: false };
  const result = cutsceneSlots(lines, [mapped, unknown]);
  assert.deepEqual(result.get(1), [mapped]);
  assert.deepEqual(result.get(2), [unknown]);
});
