import test from 'node:test';
import assert from 'node:assert/strict';
import { latestMapRoots } from '../src/data/mapSnapshots.ts';

const map = (id, version, world = 8, layer = 'gravity:1') => ({
  id,
  game_version: version,
  game_map_id: world,
  layer,
});

test('regions use numeric version order and keep inverted layers distinct', () => {
  const maps = [
    map(1, '3.9.0'),
    map(2, '3.10.0'),
    map(3, '3.10.0', 8, 'gravity:2'),
    map(4, '3.10.0', 8, 'floor:1'),
    map(5, '3.10.0', 9),
  ];
  assert.deepEqual(
    latestMapRoots(maps).map((m) => m.id),
    [2, 3, 5],
  );
  assert.equal(maps.length, 5);
});

test('reimported regions use the newest row without hiding other available regions', () => {
  assert.deepEqual(
    latestMapRoots([map(1, '3.7.0'), map(2, '3.7.0'), map(3, '3.6.0', 9)]).map((m) => m.id),
    [2, 3],
  );
  assert.deepEqual(latestMapRoots([]), []);
});
