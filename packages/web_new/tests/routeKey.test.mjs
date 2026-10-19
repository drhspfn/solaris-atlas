import assert from 'node:assert/strict';
import test from 'node:test';

import { decodeRouteKey, publicPath, questPath } from '../src/lib/routeKey.ts';

test('encoded and decoded character links use the same canonical ID and API URL', () => {
  for (const segment of ['character%3A1102', 'character:1102']) {
    const key = decodeRouteKey(segment);
    assert.equal(key, 'character:1102');
    assert.equal(
      `/characters/${encodeURIComponent(key)}/profile?locale=en`,
      '/characters/character%3A1102/profile?locale=en',
    );
  }
});

test('other canonical IDs, Unicode and numeric quest IDs decode once', () => {
  assert.equal(decodeRouteKey('item%3A2'), 'item:2');
  assert.equal(decodeRouteKey('location%3A%E9%87%91%E5%B7%9E'), 'location:金州');
  assert.equal(decodeRouteKey('10010001'), '10010001');
  assert.equal(decodeRouteKey('node%253A2'), 'node%3A2');
});

test('malformed percent escapes do not crash page or metadata generation', () => {
  assert.equal(decodeRouteKey('character%ZZ1102'), 'character%ZZ1102');
  assert.equal(encodeURIComponent(decodeRouteKey('character%ZZ1102')), 'character%25ZZ1102');
});

test('public links use stored slugs and retain system keys as a fallback', () => {
  assert.equal(
    publicPath('items', { canonical_key: 'item:3', slug: '9007199254740993-astrite' }),
    '/items/9007199254740993-astrite',
  );
  assert.equal(publicPath('items', { canonical_key: 'item:3' }), '/items/item%3A3');
  assert.equal(questPath({ game_quest_id: 3, slug: '840-the-arrival' }), '/quests/840-the-arrival');
  assert.equal(questPath({ game_quest_id: 3 }), '/quests/3');
  assert.equal(questPath({ quest: 'quest:3', slug: '840-the-arrival' }), '/quests/840-the-arrival');
  assert.equal(
    questPath({ quest_id: 3, quest_href: '/quests/840-the-arrival' }),
    '/quests/840-the-arrival',
  );
  assert.equal(
    publicPath('items', { canonical_key: 'item:3', href: 'https://example.test' }),
    '/items/item%3A3',
  );
});
