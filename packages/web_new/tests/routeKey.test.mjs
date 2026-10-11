import assert from 'node:assert/strict';
import test from 'node:test';

import { decodeRouteKey } from '../src/lib/routeKey.ts';

test('encoded and decoded character links use the same canonical ID and API URL', () => {
  for (const segment of ['character%3A1102', 'character:1102']) {
    const key = decodeRouteKey(segment);
    assert.equal(key, 'character:1102');
    assert.equal(`/characters/${encodeURIComponent(key)}/profile?locale=en`,
      '/characters/character%3A1102/profile?locale=en');
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
