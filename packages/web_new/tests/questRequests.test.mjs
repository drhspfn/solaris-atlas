import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const require = createRequire(import.meta.url);
function load(relativePath, mocks, globals = {}) {
  const source = readFileSync(new URL(relativePath, import.meta.url), 'utf8');
  const code = ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true,
  } }).outputText;
  const exports = {};
  vm.runInNewContext(code, { exports, require: (id) => mocks[id] ?? require(id),
    URLSearchParams, ...globals });
  return exports;
}

// Exercise the real component's effects without a browser or another test dependency.
function questHarness({ missingTranscript = false } = {}) {
  let query = new URLSearchParams('game_version=2.0.0&locale=ru&line=dialogue%3A999');
  let storedLocale = 'en';
  const calls = [];
  const states = [];
  const effects = [];
  const frames = [];
  const focused = [];
  let stateIndex;
  let effectIndex;
  const initialProfile = { quest: { name: { content: 'SSR snapshot' } } };
  const initialTranscript = { lines: [] };
  const props = { questKey: '662686-the-sacred-breeze-so-often-breathes', initialProfile,
    initialTranscript: missingTranscript ? null : initialTranscript,
    initialSelection: { locale: 'ru', gameVersion: '2.0.0', citedLine: 'dialogue:999' } };
  const dummy = new Proxy({}, { get: () => () => null });
  const mocks = Object.fromEntries([
    'lucide-react', '@/components/dialogue/PlayerText', '@/components/story/AnalysisActions',
    '@/components/story/QuestIllustration',
    '@/components/story/QuestClassification', '@/components/story/QuestContinuity',
    '@/components/story/QuestCutscenes', '@/components/story/QuestExplanation',
    '@/components/story/QuestMediaReferences', '@/components/ui/Feedback', '@/data/entities',
  ].map((id) => [id, dummy]));
  Object.assign(mocks, {
    '@/components/story/QuestRuntimeBlock': { runtimeBlockSlots: () => new Map(), runtimeBlockAnchor: (id) => id },
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
      if (path.includes('/profile?')) return { quest: { name: { content: path } } };
      if (path.includes('/transcript?')) return { lines: [] };
      return null;
    } },
    '@/lib/router': { useSearchParams: () => query, useLocation: () => ({ key: query.toString() }), Link: 'a' },
    '@/hooks/useLocale': { useLocale: () => storedLocale },
    '@/hooks/usePlayerDisplay': { usePlayerDisplay: () => ({}) },
    '@/preferences/NarrativePreferences': { useNarrativePreferences: () => ({}) },
    '@/components/story/cutscenePlacement': { cutsceneSlots: () => new Map() },
    '@/config/settings': { APP_SETTINGS: { limits: { transcript: 2000 } } },
  });
  const { QuestPageClient } = load('../src/components/quest/QuestPageClient.tsx', mocks, {
    window: { scrollY: 0, innerHeight: 800, addEventListener() {}, removeEventListener() {} },
    document: { querySelectorAll: () => [{ dataset: { dialogueKey: query.get('line') },
      scrollIntoView() {}, focus() { focused.push(query.get('line')); } }] },
    requestAnimationFrame: (callback) => { frames.push(callback); return frames.length; },
    cancelAnimationFrame() {},
  });
  return { calls, states, initialProfile, props, focused,
    setQuery(value) { query = new URLSearchParams(value); },
    setLocale(value) { storedLocale = value; },
    async render() {
      stateIndex = effectIndex = 0;
      QuestPageClient(props);
      for (const effect of effects) {
        if (effect.run) { const run = effect.run; effect.run = null; effect.cleanup = run(); }
      }
      for (const callback of frames.splice(0)) callback();
      await new Promise((resolve) => setImmediate(resolve));
    },
  };
}

test('matching version, locale and focus reuse SSR; changed selections fetch and returning restores SSR', async () => {
  const h = questHarness();
  await h.render();
  await h.render();
  assert.equal(h.calls.length, 0);
  assert.deepEqual(h.focused, ['dialogue:999']);
  h.setQuery('game_version=1.4.0&locale=uk&line=dialogue%3A8');
  await h.render();
  assert.equal(h.calls.length, 4);
  for (const path of h.calls) {
    const { searchParams } = new URL(path, 'http://test');
    assert.equal(searchParams.get('game_version'), '1.4.0');
    assert.equal(searchParams.get('locale'), 'uk');
    assert.equal(searchParams.get('focus_line'), path.includes('/transcript?') ? 'dialogue:8' : null);
    if (path.includes('/transcript?')) {
      assert.equal(searchParams.get('include_raw'), 'false');
      assert.equal(searchParams.get('limit'), '2000');
    }
  }
  assert.notEqual(h.states[0], h.initialProfile);
  h.setQuery('game_version=2.0.0&locale=ru&line=dialogue%3A999');
  await h.render();
  assert.equal(h.calls.length, 4);
  assert.equal(h.states[0], h.initialProfile);
  h.setQuery('game_version=2.0.0&locale=ru&line=dialogue%3A1000');
  await h.render();
  assert.equal(h.calls.length, 8, 'a different focus must not reuse a truncated SSR transcript');
});

test('missing SSR data retries and stored locale changes still fetch', async () => {
  const missing = questHarness({ missingTranscript: true });
  await missing.render();
  assert.equal(missing.calls.length, 4);
  const h = questHarness();
  h.setQuery('game_version=2.0.0&line=dialogue%3A999');
  h.setLocale('ru');
  await h.render();
  assert.equal(h.calls.length, 0);
  h.setLocale('zh');
  await h.render();
  assert.equal(h.calls.length, 4);
  assert.ok(h.calls.every((path) => path.includes('locale=zh')));
});

test('shared links avoid automatic prefetch and retain explicit opt-in and navigation props', () => {
  const React = { forwardRef: (render) => (props) => render(props, null) };
  const { Link, NavLink } = load('../src/lib/router.tsx', {
    react: React, 'next/link': { __esModule: true, default: 'next-link' },
    'next/navigation': { usePathname: () => '/quests/1' },
  });
  for (const Component of [Link, NavLink]) {
    const link = Component({ to: '/quests/1?game_version=2.0.0', replace: true, scroll: false });
    assert.equal(link.props.prefetch, false);
    assert.equal(link.props.href, '/quests/1?game_version=2.0.0');
    assert.equal(link.props.replace, true);
    assert.equal(link.props.scroll, false);
    assert.equal(Component({ to: '/quests/1', prefetch: true }).props.prefetch, true);
  }
});
