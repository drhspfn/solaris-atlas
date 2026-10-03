import test from 'node:test';
import assert from 'node:assert/strict';
import { acquisitionMarkers } from '../src/data/mapAcquisition.ts';
import { compactMapLink } from '../src/state/mapPreferences.ts';

const mob = (id, drops, name = 'Wolf') => ({
  id,
  category: 'monster',
  blueprint_type: 'BP',
  metadata: { names: { en: name }, drop_item_ids: drops },
});
test('source group selects only the exact possible drops, including hidden placements', () => {
  const markers = [mob(1, [7]), mob(2, [7]), mob(3, [8]), mob(4, [7], 'Boar')];
  assert.deepEqual(
    acquisitionMarkers(markers, 7, 1).map((m) => m.id),
    [1, 2],
  );
  assert.deepEqual(acquisitionMarkers(markers, 7, 999), []);
});
test('gathering selection uses item identity and rejects invalid items', () => {
  const markers = [
    { id: 1, category: 'resource', blueprint_type: 'Plant', metadata: { item_id: 7 } },
  ];
  assert.equal(acquisitionMarkers(markers, 7, 1).length, 1);
  assert.equal(acquisitionMarkers(markers, 0, 1).length, 0);
});
test('acquisition links retain bounded numeric source IDs, never filter lists', () => {
  assert.equal(
    compactMapLink(new URLSearchParams('map=8&item=7&source=123&hide=monster')).toString(),
    'map=8&item=7&source=123',
  );
});
