import assert from 'node:assert/strict';
import test from 'node:test';
import { questForks, illustratedRuns } from '../src/components/story/questReading.ts';

const choices = [1, 2, 3].map((n) => ({
  id: `choice${n}`,
  branch: { line_ids: [`reply${n}`], continuation_line_id: 'join' },
}));
const lines = [
  { id: 'prompt', player_choices: choices },
  ...[1, 2, 3].map((n) => ({ id: `reply${n}` })),
  { id: 'join' },
];

test('three disjoint authored answers become parallel paths with one shared continuation', () => {
  const [fork] = questForks(lines);
  assert.equal(fork.source, 'prompt');
  assert.deepEqual(
    fork.paths.map((path) => path.lineIds),
    [['reply1'], ['reply2'], ['reply3']],
  );
  assert.equal(fork.continuation, 'join');
  assert.ok(fork.paths.every((path) => !path.lineIds.includes('join')));
});

test('partial pages, shared branch bodies and loops retain their source transcript', () => {
  assert.deepEqual(questForks(lines.slice(0, 2)), []);
  assert.deepEqual(
    questForks([
      {
        id: 'prompt',
        player_choices: choices.map((choice) => ({
          ...choice,
          branch: { line_ids: ['reply1'] },
        })),
      },
      ...lines.slice(1),
    ]),
    [],
  );
  assert.deepEqual(
    questForks([
      {
        id: 'prompt',
        player_choices: choices.map((choice) => ({
          ...choice,
          branch: { line_ids: ['prompt'] },
        })),
      },
    ]),
    [],
  );
});

test('illustrated scenes respect state boundaries, choices, clearing and inserted media', () => {
  const image = { id: 'bg', kind: 'image' };
  const entries = [
    { id: '1', flow_state: 'a', illustration: image },
    { id: '2', flow_state: 'a', illustration: image },
    { id: '3', flow_state: 'a', illustration: { id: 'new', kind: 'spine' } },
    { id: '4', flow_state: 'a', illustration: { id: 'clear', kind: 'clear' } },
    { id: '5', flow_state: 'b', illustration: image },
    { id: '6', flow_state: 'b', illustration: image, player_choices: choices },
  ];
  assert.deepEqual(
    illustratedRuns(entries).map((run) => run.map((line) => line.id)),
    [['1', '2', '3'], ['5']],
  );
  assert.deepEqual(
    illustratedRuns(entries, new Set(['2'])).map((run) => run.map((line) => line.id)),
    [['1'], ['2', '3'], ['5']],
  );
});
