import assert from 'node:assert/strict';
import test from 'node:test';
import { resolveApiUrl } from '../src/api/resolveApiUrl.ts';
test('absolute CDN URLs survive both API configurations', () => {
  for (const base of ['/api', 'https://api.solarisatlas.fun']) {
    const url = 'https://cdn.solarisatlas.fun/objects/hash';
    assert.equal(resolveApiUrl(base, url), url);
    assert.equal(
      resolveApiUrl(base, '//cdn.solarisatlas.fun/objects/hash'),
      '//cdn.solarisatlas.fun/objects/hash',
    );
  }
});
test('relative media uses one separator', () => {
  assert.equal(resolveApiUrl('/api/', '/files/1'), '/api/files/1');
  assert.equal(
    resolveApiUrl('https://api.solarisatlas.fun', 'files/1'),
    'https://api.solarisatlas.fun/files/1',
  );
});
