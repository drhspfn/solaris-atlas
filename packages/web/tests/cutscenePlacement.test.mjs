import assert from 'node:assert/strict';
import { test } from 'node:test';

import { cutsceneSlots } from '../src/components/story/cutscenePlacement.ts';

const lines = [
  { id: 'a', flow_state: 'state_a', action: { index: 1 } },
  { id: 'b', flow_state: 'state_a', action: { index: 3 } },
  { id: 'c', flow_state: 'state_c', action: { index: 0 } },
];
const event = (state, index, extra = {}) => ({
  kind: 'cutscene',
  flow_state: state,
  action_index: index,
  ...extra,
});
test('inserts movies between actions, including states without dialogue', () => {
  const slots = cutsceneSlots(lines, [
    event('state_a', 2),
    event('state_b', 0),
    event('state_d', 1),
  ]);
  assert.deepEqual([...slots.keys()], [1, 2, 3]);
});
test('confirmed caption transcript anchors a separate movie wrapper', () => {
  const slots = cutsceneSlots(lines, [event('state_z', 0, { transcript_states: ['state_a'] })]);
  assert.equal(slots.get(0).length, 1);
});
test('each cutscene occurs once; actions in a gap keep authored order', () => {
  const slots = cutsceneSlots(lines, [event('state_a', 2), event('state_a', 0)]);
  assert.equal([...slots.values()].flat().length, 2);
  assert.deepEqual(cutsceneSlots(lines, []).size, 0);
});

test('focused transcript windows do not append unrelated movies', () => {
  assert.equal(cutsceneSlots(lines, [event('state_z', 0)], true).size, 0);
  assert.equal(cutsceneSlots(lines, [event('state_a', 2)], true).get(1).length, 1);
});
