import test from 'node:test';
import assert from 'node:assert/strict';
import { readMapPreferences, compactMapLink } from '../src/state/mapPreferences.ts';

test('new visitors and invalid preferences start with all markers off', () => {
  for (const saved of [null, '{', '[]', 'null']) {
    assert.deepEqual(readMapPreferences(saved, new URLSearchParams(), 'chest,resource'), {
      hide: 'chest,resource',
    });
  }
});
test('show all survives reload and unsupported saved keys are ignored', () => {
  assert.deepEqual(
    readMapPreferences(
      '{"hide":"","area":"3","opacity":false,"unexpected":"x"}',
      new URLSearchParams(),
      'chest',
    ),
    { hide: '', area: '3' },
  );
});
test('old filters migrate locally while shared links stay short', () => {
  const old = new URLSearchParams({
    map: '45',
    marker: '12662',
    hide: 'x'.repeat(20000),
    q: 'flower',
    hi: '',
  });
  assert.equal(readMapPreferences(null, old, 'chest').hide.length, 20000);
  assert.equal(compactMapLink(old).toString(), 'map=45&marker=12662');
});
test('invalid and oversized IDs never enter shared URLs', () => {
  assert.equal(
    compactMapLink(new URLSearchParams({ map: 'bad', marker: '1'.repeat(10000) })).toString(),
    '',
  );
});
