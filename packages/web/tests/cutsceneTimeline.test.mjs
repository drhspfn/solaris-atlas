import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  cutsceneTimeline,
  timelineTarget,
  playbackVolume,
} from '../src/components/story/cutsceneTimeline.ts';
const clip = (id, end, next = null, start = 0) => ({
  id,
  kind: 'clip',
  asset: id,
  start,
  end,
  next,
});
const flow = {
  entry: 'intro',
  media: {},
  nodes: [
    clip('intro', 10, 'rover'),
    {
      id: 'rover',
      kind: 'choice',
      options: [
        { next: 'male', rover: 'male' },
        { next: 'female', rover: 'female' },
      ],
    },
    clip('male', 20, 'outro'),
    clip('female', 25, 'outro'),
    clip('outro', null),
  ],
};
test('includes shared clips once and follows the selected branch duration', () => {
  const path = cutsceneTimeline(flow, { rover: 'female' }, 'ask', { outro: 5 });
  assert.equal(path.total, 40);
  assert.equal(path.complete, true);
  assert.deepEqual(
    path.clips.map((c) => c.id),
    ['intro', 'female', 'outro'],
  );
  assert.deepEqual(timelineTarget(path, 37), { id: 'outro', time: 2 });
  assert.deepEqual(timelineTarget(path, 10), { id: 'female', time: 0 });
});
test('seeking stops at an unresolved choice, but explicit Rover preference bypasses it', () => {
  const unresolved = cutsceneTimeline(flow, {}, 'ask', { outro: 5 });
  assert.deepEqual(timelineTarget(unresolved, 24), { id: 'rover', time: null });
  const automatic = cutsceneTimeline(flow, {}, 'male', { outro: 5 });
  assert.deepEqual(timelineTarget(automatic, 24), { id: 'male', time: 14 });
});
test('clamps seeks, preserves source offsets, and guards unknown duration and cycles', () => {
  const one = { ...flow, entry: 'offset', nodes: [clip('offset', 15, null, 5)] };
  const path = cutsceneTimeline(one, {}, 'ask', {});
  assert.deepEqual(timelineTarget(path, -4), { id: 'offset', time: 5 });
  assert.deepEqual(timelineTarget(path, 200), { id: 'offset', time: 15 });
  assert.equal(cutsceneTimeline(flow, {}, 'male', {}).complete, false);
  assert.equal(
    cutsceneTimeline({ ...one, nodes: [clip('offset', 15, 'offset', 5)] }, {}, 'ask', {}).complete,
    false,
  );
});
test('master volume scales music independently without exceeding media bounds', () => {
  assert.equal(playbackVolume(0.7, 0.3, 'voice'), 0.7);
  assert.equal(playbackVolume(0.7, 0.3, 'music'), 0.21);
  assert.equal(playbackVolume(0.2, 0.3, 'music'), 0.06);
  assert.equal(playbackVolume(0, 1, 'music'), 0);
  assert.equal(playbackVolume(0.7, 0, 'effects'), 0.7);
});
