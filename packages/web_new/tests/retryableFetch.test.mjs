import assert from 'node:assert/strict';
import test from 'node:test';
import { retryableFetch } from '../src/api/retryableFetch.ts';

function immediateTimers(t) {
  const delays = [];
  const timer = globalThis.setTimeout;
  t.mock.method(globalThis, 'setTimeout', (callback, delay) => {
    delays.push(delay);
    return timer(callback, 0);
  });
  return delays;
}
const busy = () => new Response('busy', { status: 503, headers: { 'Retry-After': '2' } });

test('temporary read overload respects Retry-After and recovers', async (t) => {
  const delays = immediateTimers(t);
  const signal = new AbortController().signal;
  let attempts = 0;
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    assert.equal(url, '/api/characters/1409/model');
    assert.equal(options.signal, signal);
    return ++attempts === 1 ? busy() : new Response('{"status":"ready"}');
  });
  const response = await retryableFetch('/api/characters/1409/model', { method: 'GET', signal });
  assert.equal(response.status, 200);
  assert.equal(attempts, 2);
  assert.equal(delays.length, 1);
  assert.ok(delays[0] >= 2000 && delays[0] <= 2250);
});

test('sustained overload stops after two retries', async (t) => {
  const delays = immediateTimers(t);
  let attempts = 0;
  t.mock.method(globalThis, 'fetch', async () => { attempts++; return busy(); });
  assert.equal((await retryableFetch('/api/model', {})).status, 503);
  assert.equal(attempts, 3);
  assert.equal(delays.length, 2);
});

test('mutations and responses without a short explicit retry never repeat', async (t) => {
  const delays = immediateTimers(t);
  for (const [method, status, retry] of [
    ['POST', 503, '2'], ['PATCH', 503, '2'], ['GET', 500, '2'],
    ['GET', 429, '2'], ['GET', 503, null], ['GET', 503, 'invalid'],
    ['GET', 503, '600'], ['GET', 503, '-1'],
  ]) {
    let attempts = 0;
    t.mock.method(globalThis, 'fetch', async () => {
      attempts++;
      return new Response('error', { status, headers: retry === null ? {} : { 'Retry-After': retry } });
    });
    assert.equal((await retryableFetch('/api/model', { method })).status, status);
    assert.equal(attempts, 1);
  }
  assert.equal(delays.length, 0);
});

test('aborting the backoff clears its timer and never issues another request', async (t) => {
  const controller = new AbortController();
  let waiting;
  const scheduled = new Promise((resolve) => { waiting = resolve; });
  let cleared = false;
  t.mock.method(globalThis, 'setTimeout', () => { waiting(); return 123; });
  t.mock.method(globalThis, 'clearTimeout', (timer) => { assert.equal(timer, 123); cleared = true; });
  let attempts = 0;
  t.mock.method(globalThis, 'fetch', async () => { attempts++; return busy(); });
  const request = retryableFetch('/api/model', { signal: controller.signal });
  await scheduled;
  const reason = new DOMException('Unmounted', 'AbortError');
  controller.abort(reason);
  await assert.rejects(request, (error) => error === reason);
  assert.equal(attempts, 1);
  assert.equal(cleared, true);
});
