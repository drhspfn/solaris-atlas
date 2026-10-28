import assert from 'node:assert/strict';
import test from 'node:test';
import { adsTxt, publisherId } from '../src/lib/adsense.ts';

test('one validated publisher ID produces the Google ads.txt seller record', () => {
  const client = publisherId(' ca-pub-5630632993844792 ');
  assert.equal(client, 'ca-pub-5630632993844792');
  assert.equal(adsTxt(client), 'google.com, pub-5630632993844792, DIRECT, f08c47fec0942fa0\n');
  for (const invalid of ['', undefined, 'pub-5630632993844792', 'ca-pub-123', 'ca-pub-5630632993844792\nother']) {
    assert.equal(publisherId(invalid), null);
    assert.equal(adsTxt(publisherId(invalid)), null);
  }
});
