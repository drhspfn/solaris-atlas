import assert from 'node:assert/strict';
import test from 'node:test';
import { atlasUVs } from '../src/components/story/openSceneTexture.ts';

test('atlas trimming maps original image coordinates onto the packed rectangle', () => {
  const region = { page: 'p', size: [100, 100], bounds: [10, 20, 20, 30], offsets: [5, 7, 40, 50] };
  assert.deepEqual(atlasUVs([5 / 40, 13 / 50, 25 / 40, 43 / 50], region), [0.1, 0.2, 0.3, 0.5]);
});
test('quarter-turn atlas packing preserves top-left UV orientation', () => {
  const region = { page: 'p', size: [100, 100], bounds: [10, 20, 20, 30], rotate: 90 };
  assert.deepEqual(
    atlasUVs([0, 0, 1, 0, 1, 1, 0, 1], region),
    [0.1, 0.4, 0.1, 0.2, 0.4, 0.2, 0.4, 0.4],
  );
});
test('unsupported rotations and zero-sized pages never produce invalid GPU coordinates', () => {
  assert.throws(() => atlasUVs([0, 0], { page: 'p', size: [0, 10], bounds: [0, 0, 1, 1] }));
  assert.throws(() =>
    atlasUVs([0, 0], { page: 'p', size: [10, 10], bounds: [0, 0, 1, 1], rotate: 45 }),
  );
});
