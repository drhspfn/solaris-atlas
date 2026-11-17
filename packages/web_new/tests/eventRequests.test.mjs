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
const animationPreview = load('../src/components/ui/animationPreview.ts', {}, { URL });
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
    '../ui/Skeleton': { Skeleton: 'Skeleton', SkeletonGroup: 'SkeletonGroup' },
    '../ui/RenderedAnimation': { RenderedAnimation: ({ fallback }) => fallback },
    '../ui/animationPreview': animationPreview,
    '@/api/client': { api: (url, options) => new Promise((resolve, reject) => requests.push({ url, options, resolve, reject })) },
    '@/components/dialogue/PlayerText': { PlayerText: ({ value, fallback = '' }) => value || fallback },
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
    '@/api/serverApi': { serverApi: async (url) => { requests.push(url); return url.endsWith('/snapshots') ? { versions: ['3.8.0'] } : { events: [{ ...summary, slug: '7-event' }] }; } },
    '@/components/events/EventsClient': { EventsPage: () => null },
  });
  const tree = await Page({ searchParams: Promise.resolve({ event: '7-event' }) });
  assert.deepEqual(requests, ['/events?server=europe&summary=true', '/events/snapshots']);
  assert.equal(tree.props.versions.join(','), '3.8.0');
  assert.equal(tree.props.initialEventIds.join(','), '7');
  assert.equal(tree.props.initialData.events.length, 1);
});

test('catalog shows one card and keeps independent undated periods selectable in its history', () => {
  const now = Date.now();
  const day = 86_400_000;
  const base = { ...summary, detail_url: undefined, source_id: 'activity:7',
    description: null, game_version: null, catalog_version: '3.8.0',
    banner_path: null, source_url: '', rewards: null, season: null };
  const active = { ...base, occurrence_id: 1,
    starts_at: new Date(now - day).toISOString(), ends_at: new Date(now + day).toISOString() };
  const unknown = [2, 3].map((occurrence_id) => ({ ...base, occurrence_id,
    starts_at: null, ends_at: new Date(now + 2 * day).toISOString() }));
  const historical = Array.from({ length: 1900 }, (_, i) => ({ ...base, occurrence_id: i + 4,
    starts_at: new Date(Date.UTC(1900, 0, i + 1)).toISOString(),
    ends_at: new Date(Date.UTC(1900, 0, i + 2)).toISOString() }));
  const events = [...unknown, ...historical, active];
  let stateIndex = 0;
  const states = [];
  const hooks = {
    useState(value) {
      const i = stateIndex++;
      if (!(i in states)) states[i] = value;
      return [states[i], (next) => { states[i] = typeof next === 'function' ? next(states[i]) : next; }];
    },
    useRef: (value) => ({ current: value }),
    useMemo: (fn) => fn(),
    useEffect() {},
  };
  const noOp = () => null;
  const { EventsPage } = load('../src/components/events/EventsClient.tsx', {
    react: hooks,
    '@/styles/events.css': {},
    'lucide-react': { ChevronLeft: noOp, ChevronRight: noOp, X: noOp },
    '@/api/client': { api: noOp },
    '@/components/dialogue/PlayerText': { PlayerText: noOp },
    '@/data/gameText': { gameTextPlainText: (value) => value },
    '@/hooks/usePlayerDisplay': { usePlayerDisplay: () => ({}) },
    '@/components/ui/Select': { Select: noOp },
    '@/components/ui/Feedback': { ErrorPanel: noOp },
    '@/components/ui/Skeleton': { Skeleton: noOp, SkeletonGroup: noOp },
    './EventDetails': { EventDetails: noOp },
    './EventArtwork': { EventArtwork: noOp },
    './EventArchiveHeader': { EventArchiveHeader: noOp },
    './calendar': calendar,
  });
  const render = () => { stateIndex = 0; return EventsPage({ initialData: { events, source_url: '' } }); };
  const grids = nodes(render()).filter((node) => node.props?.className === 'events-catalog-grid');
  const cards = grids.flatMap((grid) => nodes(grid).filter((node) => node.type === 'button'));
  assert.equal(cards.length, 1);
  assert(nodes(cards[0]).some((node) => node.type === 'small' &&
    [node.props.children].flat(Infinity).includes(' · 1903 periods')));
  cards[0].props.onClick({ currentTarget: {} });
  const details = nodes(render()).find((node) => node.type === noOp && node.props?.history);
  assert.equal(details.props.event.occurrence_id, active.occurrence_id, 'card opens the active period');
  assert.equal(details.props.history.length, 1903);

  const detail = harness();
  detail.props.event = details.props.event;
  detail.props.history = details.props.history;
  const history = nodes(detail.render()).find((node) => node.props?.className === 'events-history');
  const undatedButtons = nodes(history).filter((node) => node.type === 'button' &&
    typeof node.props.children === 'string' && node.props.children.startsWith('Dates not recorded'));
  assert.equal(undatedButtons.length, 2);
  undatedButtons.forEach((button) => button.props.onClick());
  assert.deepEqual(detail.selections.map((event) => event.occurrence_id), [2, 3]);
  detail.close();
});

test('pool gallery retains the canonical catalog header instead of a single option title', async () => {
  const h = harness();
  h.props.event = { ...summary, occurrence_id: null, selectable_pool_id: 100,
    title: 'Selectable Resonator Convene', featured_names: ['First', 'Second'] };
  h.render();
  h.requests[0].resolve({ ...summary, title: 'First', featured_names: ['First'], artwork: [] });
  await flush();
  const heading = nodes(h.render()).find(node => node.type === 'h2');
  assert.equal(heading.props.children.props.value, 'Selectable Resonator Convene');
  h.close();
});

test('selection loads full gallery, retains image selection and cancels on close', async () => {
  const h = harness();
  assert(nodes(h.render()).some((n) => n.type?.name === 'EventDetailsSkeleton'));
  assert.equal(h.requests.length, 1);
  assert.equal(h.requests[0].url, summary.detail_url);
  h.requests[0].resolve({ ...summary, detail_url: undefined,
    artwork: [{ path: 'full', role: 'content', url: 'full.png' }] });
  await flush();
  assert.equal(nodes(h.render()).some((n) => n.type?.name === 'EventDetailsSkeleton'), false);
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


test('details display a known end without inventing an opening date', () => {
  const h = harness();
  h.props.event = { ...summary, ends_at: '2026-12-21T10:59:00Z' };
  const date = nodes(h.render()).find((n) => n.props?.className === 'events-detail-dates');
  assert.match(date.props.children, /Start date not recorded/);
  assert.match(date.props.children, /Dec 21, 2026/);
  h.close();
});


test('details identify an estimated version start', () => {
  const h = harness();
  h.props.event = { ...summary, starts_at: '2026-11-11T10:59:59Z', ends_at: '2026-12-21T10:59:00Z',
    start_estimate: {basis: 'previous_version_banner_end', occurrence_id: 1, version: '3.7', server: 'europe'} };
  const date = nodes(h.render()).find((n) => n.props?.className === 'events-detail-dates');
  assert.match(date.props.children, /Estimated start/);
  h.close();
});
