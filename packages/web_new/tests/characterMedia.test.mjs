import assert from 'node:assert/strict';
import test from 'node:test';
import { collectCharacterMedia } from '../src/components/characters/characterMediaItems.ts';

const normalize = (url) => url.replace(/^\/api/, '');
const art = (kind, url, scope = 'character') => ({ kind, url, scope, engine_path: kind });

test('collects distinct character artwork and keeps shared references outside the gallery', () => {
  const items = collectCharacterMedia([
    art('RoleStand', '/api/stand.png'),
    art('RolePortrait', '/api/shared.png', 'shared_resource'),
    art('RoleHeadIconLarge', null),
    art('RoleStand', '/stand.png'),
  ], '/api/stand.png', 'Rover', 'https://cdn.test/idle.webm', normalize);
  assert.equal(items.length, 2);
  assert.deepEqual(items.map((item) => item.title), ['Role Stand', 'Trial animation']);
  assert.equal(items[1].poster, '/stand.png');
  assert.equal(items[1].type, 'video');
});

test('empty media and an independent portrait are handled', () => {
  assert.deepEqual(collectCharacterMedia([], null, 'Rover', null, normalize), []);
  assert.equal(collectCharacterMedia([], '/api/portrait.png', 'Rover', null, normalize)[0].title, 'Rover portrait');
});


test('selection follows its source when items change order or disappear', async () => {
  const { characterMediaIndex } = await import('../src/components/characters/characterMediaItems.ts');
  const first = { src: '/first' };
  const second = { src: '/second' };
  assert.equal(characterMediaIndex([first, second], '/second'), 1);
  assert.equal(characterMediaIndex([second, first], '/second'), 0);
  assert.equal(characterMediaIndex([first], '/second'), null);
});
