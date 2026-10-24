import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const require = createRequire(import.meta.url);
function load(path, mocks, globals = {}) {
  const code = ts.transpileModule(readFileSync(new URL(path, import.meta.url), 'utf8'), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const exports = {};
  vm.runInNewContext(code, { exports, require: (id) => mocks[id] ?? require(id), ...globals });
  return exports;
}
const calendar = load('../src/components/events/calendar.ts', {});
const summary = { id: 7, occurrence_id: 42, title: 'Event', kind: 'limited',
  starts_at: null, ends_at: null, artwork: [], detail_url: '/events/7/detail?server=asia&occurrence_id=42' };
function harness() {
  let stateIndex, effectIndex;
  const states = [], effects = [], requests = [], selections = [];
  const props = { event: summary, history: [], timeZone: 'UTC', now: 0,
    onSelect: (event) => selections.push(event), onClose() {} };
  const { EventDetails } = load('../src/components/events/EventDetails.tsx', {
    react: {
      useState(value) {
        const i = stateIndex++;
        if (!(i in states)) states[i] = value;
        return [states[i], (next) => { states[i] = typeof next === 'function' ? next(states[i]) : next; }];
      },
      useRef: () => ({ current: { showModal() {}, close() {} } }),
      useEffect(run, deps) {
        const i = effectIndex++, previous = effects[i];
        if (!previous || deps.some((d, j) => d !== previous.deps[j])) {
          previous?.cleanup?.(); effects[i] = { deps, run };
        }
      },
    },
    'lucide-react': { X: () => null, ExternalLink: () => null },
    './EventArtwork': { EventArtwork: () => null }, './calendar': calendar,
    '@/api/client': { api: (url, options) => new Promise((resolve, reject) => requests.push({ url, options, resolve, reject })) },
  }, { AbortController, document: { body: { style: { overflow: '' } } },
    setInterval: () => 1, clearInterval() {} });
  return { props, requests, selections,
    render() {
      stateIndex = effectIndex = 0;
      const tree = EventDetails(props);
      for (const effect of effects) {
        if (effect.run) { const run = effect.run; effect.run = null; effect.cleanup = run(); }
      }
      return tree;
    },
    close() { effects.forEach((e) => e.cleanup?.()); },
  };
}
function nodes(tree) {
  if (!tree || typeof tree !== 'object') return [];
  return [tree, ...[tree.props?.children].flat(Infinity).flatMap(nodes)];
}
const flush = () => new Promise((resolve) => setImmediate(resolve));

test('SSR requests summary and resolves slug filters without a detail request', async () => {
  const requests = [];
  const { default: Page } = load('../src/app/events/page.tsx', {
    'next/server': { connection: async () => {} },
    '@/api/serverApi': { serverApi: async (url) => { requests.push(url); return { events: [{ ...summary, slug: '7-event' }] }; } },
    '@/components/events/EventsClient': { EventsPage: () => null },
  });
  const tree = await Page({ searchParams: Promise.resolve({ event: '7-event' }) });
  assert.deepEqual(requests, ['/events?server=europe&summary=true']);
  assert.equal(tree.props.initialEventIds.join(','), '7');
  assert.equal(tree.props.initialData.events.length, 1);
});

test('selection loads full gallery, retains image selection and cancels on close', async () => {
  const h = harness();
  assert(nodes(h.render()).some((n) => n.props?.role === 'status'));
  assert.equal(h.requests.length, 1);
  assert.equal(h.requests[0].url, summary.detail_url);
  h.requests[0].resolve({ ...summary, detail_url: undefined,
    artwork: [{ path: 'full', role: 'content', url: 'full.png' }] });
  await flush();
  const button = nodes(h.render()).find((n) => n.props?.['aria-label'] === 'Preview artwork 1');
  assert(button);
  button.props.onClick();
  assert.equal(h.selections[0].preview_url, 'full.png');
  assert.equal(h.selections[0].artwork.length, 1);
  h.props.event = h.selections[0]; h.render();
  assert.equal(h.requests.length, 1, 'image selection must not reload the gallery');
  h.close();
  assert(h.requests[0].options.signal.aborted);
});

test('changing runs cancels stale responses; failures offer working retry', async () => {
  const h = harness(); h.render();
  h.props.event = { ...summary, occurrence_id: 43, detail_url: '/events/7/detail?server=asia&occurrence_id=43' };
  h.render();
  assert(h.requests[0].options.signal.aborted);
  h.requests[0].resolve({ ...summary, artwork: [{ path: 'stale', role: 'content', url: 'stale.png' }] });
  h.requests[1].reject(new Error('Gallery unavailable')); await flush();
  const tree = h.render();
  assert(nodes(tree).some((n) => n.props?.role === 'alert'));
  assert(!nodes(tree).some((n) => n.props?.['aria-label'] === 'Preview artwork 1'));
  nodes(tree).find((n) => n.type === 'button' && n.props.children === 'Retry event artwork').props.onClick();
  h.render(); assert.equal(h.requests.length, 3);
  h.requests[2].resolve({ ...h.props.event, artwork: [] }); await flush();
  assert(!nodes(h.render()).some((n) => ['status', 'alert'].includes(n.props?.role)));
  h.close();
});



test('event detail dates and reward quantities retain valid UTF-8 typography', () => {
  const source = readFileSync(new URL('../src/components/events/EventDetails.tsx', import.meta.url));
  new TextDecoder('utf-8', { fatal: true }).decode(source);
  const h = harness();
  h.props.event = { ...summary, detail_url: undefined, game_version: '3.7',
    starts_at: '2026-10-01T00:00:00Z', ends_at: '2026-10-02T00:00:00Z',
    rewards: [{ id: 1, name: 'Astrite', value: 2 }] };
  const html = require('react-dom/server').renderToStaticMarkup(h.render());
  assert(html.includes('\u00b7 v3.7'));
  assert(html.includes(' \u2192 '));
  assert(html.includes(' \u00d72'));
  h.close();
});
