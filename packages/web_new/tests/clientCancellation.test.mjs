import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

function load(fetch) {
  const exports = {};
  const mocks = {
    '../config/settings': { APP_SETTINGS: { api: { defaultBase: '/api' } } },
    './requestPolicy': { requestCredentials: () => 'omit' },
    './resolveApiUrl': {},
    './retryableFetch': { retryableFetch: fetch },
  };
  const code = ts.transpileModule(readFileSync(new URL('../src/api/client.ts', import.meta.url), 'utf8'),
    { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  vm.runInNewContext(code, { exports, require: (id) => mocks[id], process: { env: {} }, window: {}, Headers, Blob, fetch, AbortSignal });
  return exports.api;
}

test('cancellable consumers never share an abort-owned request or poison the shared cache', async () => {
  const calls = [];
  const api = load((_url, options) => new Promise((resolve, reject) => {
    calls.push({ options, resolve });
    options.signal?.addEventListener('abort', () => reject(options.signal.reason), { once: true });
  }));
  const controller = new AbortController();
  const cancelled = api('/catalog', { signal: controller.signal });
  const shared = api('/catalog');
  const subscriber = api('/catalog');
  assert.equal(calls.length, 2);
  controller.abort();
  await assert.rejects(cancelled, { name: 'AbortError' });
  calls[1].resolve(Response.json({ results: ['current'] }));
  assert.deepEqual(await shared, { results: ['current'] });
  assert.deepEqual(await subscriber, await shared);
  await api('/catalog');
  assert.equal(calls.length, 2);
});

test('cacheTtl zero forces a fresh request instead of returning an old cached response', async () => {
  let calls = 0;
  const api = load(async () => Response.json({ revision: ++calls }));
  assert.equal((await api('/catalog')).revision, 1);
  assert.equal((await api('/catalog')).revision, 1);
  assert.equal((await api('/catalog', { cacheTtl: 0 })).revision, 2);
});

test('projection-pending responses use a human message for legacy and structured backend contracts', async () => {
  for (const detail of ['Public read projection is not ready for this selection',
    { code: 'read_projection_pending', message: 'Projection is not published' }]) {
    const api = load(async () => Response.json({ detail }, { status: 503 }));
    await assert.rejects(api('/catalog'), (error) => {
      assert.equal(error.status, 503);
      assert.equal(error.code, 'read_projection_pending');
      assert.equal(error.message, 'This archive selection is being prepared. Please try again shortly.');
      return true;
    });
  }
  await assert.rejects(load(async () => Response.json({ detail: 'Other unavailable service' }, { status: 503 }))('/catalog'),
    (error) => error.message === 'Other unavailable service' && error.code === undefined);
});

test('public reads have a deadline even without a caller signal', async (t) => {
  const deadline = new AbortController();
  t.mock.method(AbortSignal, 'timeout', (ms) => {
    assert.equal(ms, 30_000);
    return deadline.signal;
  });
  const api = load((_url, options) => new Promise((_resolve, reject) => {
    options.signal.addEventListener('abort', () => reject(options.signal.reason), { once: true });
  }));
  const request = api('/catalog');
  deadline.abort(new DOMException('Timed out', 'TimeoutError'));
  await assert.rejects(request, { name: 'TimeoutError' });
});

test('a deadline during response-body parsing is not swallowed as a successful null', async (t) => {
  const deadline = new AbortController();
  t.mock.method(AbortSignal, 'timeout', () => deadline.signal);
  let reading;
  const bodyStarted = new Promise((resolve) => { reading = resolve; });
  const api = load(async (_url, options) => ({
    ok: true,
    json: () => new Promise((_resolve, reject) => {
      options.signal.addEventListener('abort', () => reject(options.signal.reason), { once: true });
      reading();
    }),
  }));
  const request = api('/catalog');
  await bodyStarted;
  deadline.abort(new DOMException('Timed out', 'TimeoutError'));
  await assert.rejects(request, { name: 'TimeoutError' });
});
