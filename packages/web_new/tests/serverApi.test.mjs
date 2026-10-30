import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const code = ts.transpileModule(
  readFileSync(new URL('../src/api/serverApi.ts', import.meta.url), 'utf8'),
  { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } },
).outputText;

function load(fetch) {
  const exports = {};
  const timeouts = [];
  // Emulate a single React server render's cache lifetime.
  const cache = (fn) => {
    const reads = new Map();
    return (...args) => {
      const key = JSON.stringify(args);
      if (!reads.has(key)) reads.set(key, fn(...args));
      return reads.get(key);
    };
  };
  vm.runInNewContext(code, {
    exports, require: () => ({ cache }), fetch,
    process: { env: { INTERNAL_API_BASE: 'http://backend:8000/' } },
    AbortSignal: { timeout: (ms) => { timeouts.push(ms); return AbortSignal.timeout(ms); } },
    console: { error() {} },
  });
  return { api: exports.serverApi, timeouts };
}

test('metadata and page share bounded reads with equivalent options, preserving ISR', async () => {
  const calls = [];
  const { api, timeouts } = load(async (url, options) => {
    calls.push({ url, options });
    return Response.json({ name: 'Character' });
  });
  const [metadata, page] = await Promise.all([
    api('/api/characters/1/profile'), api('/api/characters/1/profile'),
  ]);
  assert.equal(metadata, page);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, 'http://backend:8000/characters/1/profile');
  assert.equal(calls[0].options.next.revalidate, 60);
  assert.deepEqual(timeouts, [15_000]);
  assert.equal(calls[0].options.signal.aborted, false);

  await Promise.all([api('/notices/1', { revalidate: 0 }), api('/notices/1', { revalidate: 0 })]);
  assert.equal(calls.length, 2);
  assert.equal(calls[1].options.next.revalidate, 0);
  await api('/notices/1', { revalidate: 60 });
  assert.equal(calls.length, 3);
});

test('HTTP failures, timeouts and unreadable bodies permit client recovery', async () => {
  for (const fetch of [
    async () => new Response('', { status: 503 }),
    async () => { throw new DOMException('Timed out', 'TimeoutError'); },
    async () => new Response('not json'),
    async () => ({ ok: true, json: async () => { throw new DOMException('Timed out', 'TimeoutError'); } }),
  ]) {
    assert.equal(await load(fetch).api('/catalog'), null);
  }
});
