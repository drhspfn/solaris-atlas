import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const require = createRequire(import.meta.url);

// Exercise the actual component effects using the same lightweight harness as questRequests.
function nodeHarness({ incomplete = false, holdRequests = false } = {}) {
  let query = new URLSearchParams();
  let locale = 'en';
  const calls = [];
  const pending = [];
  const states = [];
  const effects = [];
  let stateIndex, effectIndex;
  const initialDetail = { canonical_key: 'dialogue:1', status: 'active' };
  const initialRelated = [];
  const initialNarrative = { available: false };
  const props = { nodeKey: 'dialogue:1', initialDetail,
    initialRelated: incomplete ? undefined : initialRelated, initialNarrative };
  const dummy = new Proxy({}, { get: () => () => null });
  const mocks = Object.fromEntries([
    'lucide-react', '@/components/dialogue/PlayerText', '@/components/ui/Feedback',
    '@/components/story/cutsceneAnchor', '@/data/entities', '@/data/story',
  ].map((id) => [id, dummy]));
  Object.assign(mocks, {
    react: { useState(value) {
      const index = stateIndex++;
      if (!(index in states)) states[index] = value;
      return [states[index], (next) => { states[index] = next; }];
    }, useEffect(run, deps) {
      const index = effectIndex++;
      const previous = effects[index];
      if (!previous || deps.some((value, i) => value !== previous.deps[i])) {
        previous?.cleanup?.();
        effects[index] = { deps, run };
      }
    } },
    '@/api/client': { api: async (path) => {
      calls.push(path);
      const value = path.includes('/related?') ? { results: [] }
        : path.includes('/narrative-context?') ? { available: true, dialogue: {} }
          : { canonical_key: 'dialogue:updated', status: 'active' };
      if (holdRequests) return new Promise((resolve) => pending.push(() => resolve(value)));
      return value;
    } },
    '@/lib/router': { useSearchParams: () => query, Link: 'a' },
    '@/hooks/useLocale': { useLocale: () => locale },
    '@/hooks/usePlayerDisplay': { usePlayerDisplay: () => ({}) },
    '@/data/localized': { localizedText: (_value, fallback) => fallback },
    '@/data/playerName': { interpolatePlayerName: (value) => value },
    '@/config/settings': { APP_SETTINGS: { limits: { relatedNodes: 100 } } },
  });
  const source = readFileSync(new URL('../src/components/nodes/NodeExplorerClient.tsx', import.meta.url), 'utf8');
  const code = ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true,
  } }).outputText;
  const exports = {};
  vm.runInNewContext(code, { exports, require: (id) => mocks[id] ?? require(id), URLSearchParams, AbortController });
  return { calls, states, initialDetail, initialRelated,
    setQuery(value) { query = new URLSearchParams(value); },
    setLocale(value) { locale = value; },
    resolveRequests() { pending.splice(0).forEach((resolve) => resolve()); },
    async render() {
      stateIndex = effectIndex = 0;
      exports.NodeExplorerPage(props);
      for (const effect of effects) {
        if (effect.run) { const run = effect.run; effect.run = null; effect.cleanup = run(); }
      }
      await new Promise((resolve) => setImmediate(resolve));
    },
  };
}

test('matching node SSR stays visible; changed selections fetch and returning restores it', async () => {
  const h = nodeHarness();
  await h.render();
  await h.render();
  assert.equal(h.calls.length, 0);
  assert.equal(h.states[0], h.initialDetail);
  assert.equal(h.states[3], false);

  h.setQuery('game_version=2.1.0&locale=ru');
  await h.render();
  assert.equal(h.calls.length, 3);
  assert.ok(h.calls.some((path) => path.includes('/narrative-context?locale=ru&game_version=2.1.0')));
  assert.notEqual(h.states[0], h.initialDetail);

  h.setQuery('');
  await h.render();
  assert.equal(h.calls.length, 3);
  assert.equal(h.states[0], h.initialDetail);
  assert.equal(h.states[1], h.initialRelated);
  assert.equal(h.states[2], null);
  assert.equal(h.states[3], false);
  h.setLocale('ja');
  await h.render();
  assert.equal(h.calls.length, 6);
  assert.ok(h.calls.some((path) => path.includes('/related?locale=ja')));
});

test('incomplete server data is fetched instead of treated as a complete node', async () => {
  const h = nodeHarness({ incomplete: true });
  await h.render();
  assert.equal(h.calls.length, 3);
});

test('late responses cannot overwrite restored server data', async () => {
  const h = nodeHarness({ holdRequests: true });
  await h.render();
  h.setQuery('locale=ru');
  await h.render();
  assert.equal(h.calls.length, 3);
  h.setQuery('');
  await h.render();
  h.resolveRequests();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(h.states[0], h.initialDetail);
  assert.equal(h.states[1], h.initialRelated);
  assert.equal(h.states[3], false);
});
