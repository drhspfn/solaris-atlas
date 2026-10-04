import assert from 'node:assert/strict';
import test from 'node:test';

import {
  canMarkFound,
  createMapProgressStore,
  mapProgressKey,
  readMapProgress,
} from '../src/state/mapProgress.ts';

const marker = (category, name) => ({
  entity_id: 120,
  category,
  metadata: { names: { en: name } },
});
function memoryStorage() {
  const values = new Map();
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
  };
}

test('only chests and recognized one-time collectibles can be marked', () => {
  for (const item of [
    marker('chest', 'Basic Supply Chest'),
    marker('chest', 'Tidal Heritage'),
    marker('collectible', 'Sonance Casket'),
    marker('collectible', 'Sonance Casket: Ragunna'),
    marker('collectible', 'Windchimer'),
    marker('collectible', 'Unclaimed Rafter Kite'),
  ])
    assert.equal(canMarkFound(item), true, item.metadata.names.en);
  for (const item of [
    marker('collectible', 'Floramber'),
    marker('collectible', 'Scarletthorn'),
    marker('collectible', 'Septimont Sonance Casket Collector'),
    marker('collectible', 'Auto Casket'),
    marker('collectible', undefined),
    marker('exploration', 'Rafter Kite Retrieval'),
    ...['resource', 'teleport', 'boss', 'monster', 'tacet_field', 'hologram', 'shop'].map(
      (category) => marker(category, 'Sonance Casket'),
    ),
  ])
    assert.equal(canMarkFound(item), false, `${item.category}: ${item.metadata.names.en}`);
});

test('placement keys survive reimport IDs and isolate different regions', () => {
  assert.equal(
    mapProgressKey(8, { entity_id: 120, id: 1 }),
    mapProgressKey(8, { entity_id: 120, id: 900 }),
  );
  assert.notEqual(mapProgressKey(8, { entity_id: 120 }), mapProgressKey(102, { entity_id: 120 }));
});

test('damaged or unsupported storage is ignored without accepting unsafe identities', () => {
  for (const saved of [null, '{', 'null', '[]', '{"version":2,"found":["8:120"]}']) {
    assert.equal(readMapProgress(saved).found.size, 0);
    assert.equal(readMapProgress(saved).showFound, false);
  }
  const state = readMapProgress(
    JSON.stringify({
      version: 1,
      found: ['8:120', '8:120', null, '__proto__', '8:9007199254740992'],
      showFound: 'true',
    }),
  );
  assert.deepEqual([...state.found], ['8:120']);
  assert.equal(state.showFound, false);
});

test('mark, reload, show found and undo persist separately from map filters', () => {
  const storage = memoryStorage();
  const first = createMapProgressStore(storage, 'progress');
  first.setFound('8:120', true);
  first.setShowFound(true);
  const reloaded = createMapProgressStore(storage, 'progress');
  assert.equal(reloaded.getSnapshot().found.has('8:120'), true);
  assert.equal(reloaded.getSnapshot().showFound, true);
  reloaded.setFound('8:120', false);
  assert.equal(createMapProgressStore(storage, 'progress').getSnapshot().found.size, 0);
});

test('a stale tab preserves other placements and refreshes external undo', () => {
  const storage = memoryStorage();
  const first = createMapProgressStore(storage, 'progress');
  const second = createMapProgressStore(storage, 'progress');
  first.setFound('8:120', true);
  second.setFound('102:3', true);
  first.refresh();
  assert.deepEqual([...first.getSnapshot().found].sort(), ['102:3', '8:120']);
  second.setFound('8:120', false);
  first.refresh();
  assert.equal(first.getSnapshot().found.has('8:120'), false);
});

test('quota and unavailable storage keep usable session progress with explicit failure', () => {
  for (const storage of [
    null,
    {
      getItem: () => null,
      setItem: () => {
        throw new Error('QuotaExceeded');
      },
    },
  ]) {
    const store = createMapProgressStore(storage, 'progress');
    store.setFound('8:120', true);
    store.setFound('8:121', true);
    store.setShowFound(true);
    assert.equal(store.getSnapshot().found.size, 2);
    assert.equal(store.getSnapshot().showFound, true);
    assert.equal(store.getSnapshot().storageError, true);
    store.setFound('8:120', false);
    assert.equal(store.getSnapshot().found.size, 1);
  }
});
