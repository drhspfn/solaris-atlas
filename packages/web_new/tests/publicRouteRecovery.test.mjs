import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const require = createRequire(import.meta.url);
const tick = () => new Promise((resolve) => setImmediate(resolve));
const dummy = new Proxy({}, { get: () => () => null });
function compile(path, mocks, globals = {}) {
  const code = ts.transpileModule(readFileSync(new URL(path, import.meta.url), 'utf8'), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS,
      jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
  }).outputText;
  const exports = {};
  vm.runInNewContext(code, { exports, URLSearchParams, AbortController, Error,
    require: (id) => mocks[id] ?? (id === 'react/jsx-runtime' ? require(id) : dummy), ...globals });
  return exports;
}
const settings = compile('../src/config/settings.ts', {});
const calendar = compile('../src/components/events/calendar.ts', {});

test('projection-pending feedback names archive preparation and keeps a retry action', () => {
  const { ErrorPanel } = compile('../src/components/ui/Feedback.tsx', {});
  const html = require('react-dom/server').renderToStaticMarkup(ErrorPanel({
    message: 'This archive selection is being prepared. Please try again shortly.', onRetry() {},
  }));
  assert.ok(html.includes('Archive is being prepared'));
  assert.ok(html.includes('Try again'));
  assert.ok(!html.includes('Archive connection interrupted'));
  assert.ok(!html.includes('database'));
});
function nodes(tree) {
  return !tree || typeof tree !== 'object' ? []
    : [tree, ...[tree.props?.children].flat(Infinity).flatMap(nodes)];
}

// Match the repository's hook harness approach, including a transport that can
// deliver late results despite an aborted signal.
function harness(path, name, props, initialQuery = '') {
  let cursor = 0, locale = 'en', query = new URLSearchParams(initialQuery);
  const slots = [], effects = [], calls = [];
  const router = {
    Link: 'a',
    useSearchParams: () => Object.assign([query, (update) => {
      query = typeof update === 'function' ? update(query) : new URLSearchParams(update);
    }], { get: (key) => query.get(key), has: (key) => query.has(key), toString: () => query.toString() }),
  };
  const react = {
    createContext: () => ({ Provider: 'auth-provider' }),
    useCallback: (fn) => fn,
    useState(initial) {
      const i = cursor++;
      if (!(i in slots)) slots[i] = typeof initial === 'function' ? initial() : initial;
      return [slots[i], (value) => { slots[i] = typeof value === 'function' ? value(slots[i]) : value; }];
    },
    useRef(initial) { const i = cursor++; return slots[i] ??= { current: initial }; },
    useMemo: (fn) => fn(),
    useEffect(run, deps) {
      const i = cursor++, old = slots[i];
      if (!old || deps.some((value, j) => value !== old.deps[j])) {
        old?.cleanup?.(); const effect = slots[i] = { run, deps }; effects.push(effect);
      }
    },
  };
  const api = { api: (path, options) => new Promise((resolve, reject) => calls.push({ path, options, resolve, reject })) };
  const mocks = {
    react, '@/api/client': api, '../../api/client': api,
    '@/lib/router': router, '@/hooks/useLocale': { useLocale: () => locale },
    '../../hooks/useLocale': { useLocale: () => locale },
    '@/config/settings': settings, '../../config/settings': settings,
    '@/components/ui/Select': { Select: 'atlas-select' },
    '../ui/Select': { Select: 'atlas-select' },
    './calendar': calendar,
    '@/data/mapSnapshots': { latestMapRoots: (maps) => maps },
    '@/data/mapAcquisition': { acquisitionMarkers: () => [] },
    '@/state/mapPreferences': { compactMapLink: (value) => value },
    '@/hooks/useMapPreferences': { useMapPreferences: () => [{}, () => {}] },
    '@/hooks/useMapProgress': { useMapProgress: () => ({ found: new Set(), showFound: true }) },
  };
  const Component = compile(`../src/components/${path}.tsx`, mocks, {
    window: { matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }),
      setInterval: () => 1, clearInterval() {} },
    document: { title: '', body: { style: {} } }, setInterval: () => 1, clearInterval() {},
  })[name];
  return { calls,
    queryString: () => query.toString(),
    locale(value) { locale = value; },
    query(value) { query = new URLSearchParams(value); },
    render() {
      cursor = 0; const tree = Component(props);
      for (const effect of effects.splice(0)) effect.cleanup = effect.run();
      return tree;
    },
  };
}

test('story path and patch use shared selectors while retaining URL context and cancelling obsolete reads', () => {
  const initialMap = { selected_game_version: '2.1.0', quest_type_id: 0,
    imported_game_versions: ['2.1.0', '1.4.0'], chapters: [] };
  const h = harness('story/StoryMapClient', 'StoryMapClient', { initialMap }, 'game_version=2.1.0&locale=ru&line=dialogue%3A7');
  const tree = h.render();
  const selectors = nodes(tree).filter((node) => node.type === 'atlas-select');
  assert.equal(selectors.length, 2);
  assert.equal(nodes(tree).some((node) => node.type === 'select'), false);
  const path = selectors.find((node) => node.props.label === 'Story path');
  const patch = selectors.find((node) => node.props.label === 'Patch');
  assert.equal(path.props.options[0].label, 'All story paths');
  assert.equal(patch.props.options[0].label, 'All patches');
  assert.equal(patch.props.value, '2.1.0');
  assert.equal(patch.props.options[2].value, '1.4.0');
  path.props.onChange('9'); h.render();
  assert.equal(new URLSearchParams(h.queryString()).get('quest_type'), '9');
  assert.match(h.calls[0].path, /quest_type_id=9/);
  nodes(h.render()).find((node) => node.type === 'atlas-select' && node.props.label === 'Patch').props.onChange('');
  h.render();
  const query = new URLSearchParams(h.queryString());
  assert.equal(query.get('game_version'), null);
  assert.equal(query.get('quest_type'), '9');
  assert.equal(query.get('locale'), 'ru');
  assert.equal(query.get('line'), 'dialogue:7');
  assert.equal(h.calls[0].options.signal.aborted, true);
});

test('dialogue quest scope uses the shared selector and changing scope cancels stale results', async () => {
  const h = harness('search/InlineDialogueSearch', 'InlineDialogueSearch', {
    character: 'character:1', playerDisplay: {}, quests: [
      { game_quest_id: 7, name: { content: 'First appearance' } },
      { quest: { game_quest_id: 8, name: { content: 'Second appearance' } } },
    ],
  });
  h.render();
  const tree = h.render();
  assert.equal(nodes(tree).some((node) => node.type === 'select'), false);
  const selector = nodes(tree).find((node) => node.type === 'atlas-select');
  assert.equal(selector.props.label, 'Limit dialogue search to a quest');
  assert.equal(selector.props.options.map((option) => option.label).join(','),
    'All appearances,First appearance,Second appearance');
  nodes(tree).find((node) => node.type === 'input').props.onChange({ target: { value: 'Remember' } });
  nodes(h.render()).find((node) => node.type === 'form').props.onSubmit({ preventDefault() {} });
  selector.props.onChange('8'); h.render();
  assert.equal(h.calls[0].options.signal.aborted, true);
  h.calls[0].resolve({ results: [{ text: 'Obsolete' }] }); await tick();
  assert.equal(nodes(h.render()).some((node) => node.props?.className === 'quote-result'), false);
  nodes(h.render()).find((node) => node.type === 'form').props.onSubmit({ preventDefault() {} });
  assert.equal(new URL(h.calls[1].path, 'http://fixture').searchParams.get('quest_id'), '8');
});

test('cutscene list retains matching SSR and refetches only a changed selection or retry', async () => {
  const initial = { items: [], total: 0, imported_game_versions: [], next_cursor: null };
  const h = harness('story/CutsceneArchive', 'CutsceneArchive', { initial, versions: [] }, 'locale=ru');
  h.render(); h.render();
  assert.equal(h.calls.length, 0, 'explicit URL locale matches SSR regardless of saved locale');
  h.query('q=M03&locale=ru'); h.render();
  assert.equal(h.calls.length, 1);
  assert.match(h.calls[0].path, /locale=ru/);
  h.calls[0].reject(new Error('Scenes unavailable')); await tick();
  nodes(h.render()).find((node) => node.props?.message === 'Error: Scenes unavailable').props.onRetry();
  h.render(); assert.equal(h.calls.length, 2);
  h.calls[1].resolve(initial); await tick();
  assert.equal(nodes(h.render()).some((node) => node.props?.message), false);
});

test('cutscene detail restores SSR and ignores obsolete locale responses', async () => {
  const initial = { name: 'M03', title: 'Original scene', game_version: '3.7', event: {}, passages: [] };
  const h = harness('story/CutsceneArchive', 'CutsceneRecord', { name: 'M03', initial }, 'game_version=3.7');
  h.render(); assert.equal(h.calls.length, 0);
  h.locale('ru'); h.render();
  assert.equal(h.calls.length, 1);
  h.locale('en'); h.render();
  assert.equal(h.calls[0].options.signal.aborted, true);
  h.calls[0].resolve({ ...initial, title: 'Stale scene' }); await tick();
  assert.equal(nodes(h.render()).find((node) => node.type === 'h1').props.children, 'Original scene');
});

for (const [path, name, props] of [
  ['story-analysis/StoryEventClient', 'StoryEventClient', { eventKey: 'event:1', initialEvent: { title: 'Original', citations: [] } }],
  ['story-analysis/StoryConnectionClient', 'StoryConnectionClient', { documentId: 'doc:1', index: '0', initialData: { title: 'Original' } }],
]) {
  test(`${name} cancels obsolete reads and restores SSR without another fetch`, async () => {
    const h = harness(path, name, props);
    h.render(); assert.equal(h.calls.length, 0);
    h.locale('ru'); h.render(); assert.equal(h.calls.length, 1);
    h.locale('en'); h.render();
    assert.equal(h.calls[0].options.signal.aborted, true);
    h.calls[0].resolve({ title: 'Obsolete' }); await tick();
    assert.equal(nodes(h.render()).find((node) => node.type === 'h1').props.children, 'Original');
  });
}

test('story-map failure retries and returning to its SSR selection cancels a pending read', async () => {
  const initialMap = { selected_game_version: null, quest_type_id: 0, imported_game_versions: [], chapters: [] };
  const h = harness('story/StoryMapClient', 'StoryMapClient', { initialMap });
  h.render(); assert.equal(h.calls.length, 0);
  h.query('quest_type=9'); h.render();
  h.calls[0].reject(new Error('Path unavailable')); await tick();
  nodes(h.render()).find((node) => node.props?.message === 'Path unavailable').props.onRetry();
  h.render(); assert.equal(h.calls.length, 2);
  h.calls[1].resolve(initialMap); await tick();
  assert.equal(nodes(h.render()).some((node) => node.props?.message), false);

  const restore = harness('story/StoryMapClient', 'StoryMapClient', { initialMap });
  restore.render(); restore.locale('ru'); restore.render(); restore.locale('en'); restore.render();
  assert.equal(restore.calls.length, 1);
  assert.equal(restore.calls[0].options.signal.aborted, true);
});

test('inline dialogue search distinguishes request failure from no matches and can retry', async () => {
  const h = harness('search/InlineDialogueSearch', 'InlineDialogueSearch', { character: 'character:1', quests: [], playerDisplay: {} });
  h.render();
  nodes(h.render()).find((node) => node.type === 'input').props.onChange({ target: { value: 'Remember' } });
  nodes(h.render()).find((node) => node.type === 'form').props.onSubmit({ preventDefault() {} });
  h.calls[0].reject(new Error('Offline')); await tick();
  const error = nodes(h.render()).find((node) => node.props?.message === 'Offline');
  assert.ok(error); error.props.onRetry(); assert.equal(h.calls.length, 2);
  h.calls[1].resolve({ results: [] }); await tick();
  assert.ok(nodes(h.render()).some((node) => String(node.props?.children).startsWith('No matching dialogue')));
  assert.equal(nodes(h.render()).some((node) => node.props?.message), false);
});

test('events reuse SSR on mount and return, and a failed server switch exposes retry', async () => {
  const h = harness('events/EventsClient', 'EventsPage', { initialData: { events: [] } });
  h.render(); h.render(); assert.equal(h.calls.length, 0);
  nodes(h.render()).find((node) => node.props?.label === 'Server schedule').props.onChange('asia');
  h.render(); h.calls[0].reject(new Error('Schedule unavailable')); await tick();
  nodes(h.render()).find((node) => node.props?.message === 'Schedule unavailable').props.onRetry();
  h.render(); assert.equal(h.calls.length, 2);
  assert.equal(h.calls[1].path, '/events?server=asia&summary=true');
});

test('map retry uses a fresh cancellable catalog and clears errors after an empty successful response', async () => {
  const h = harness('map/WorldMapClient', 'WorldMapPage', {});
  h.render(); assert.equal(h.calls.length, 1);
  assert.equal(h.calls[0].path, '/maps');
  assert.ok(h.calls[0].options.signal);
  h.calls[0].reject(new Error('Regions unavailable')); await tick();
  nodes(h.render()).find((node) => node.type === 'button' && node.props.children === 'Retry').props.onClick();
  h.render(); assert.equal(h.calls.length, 2);
  assert.equal(h.calls[0].options.signal.aborted, true);
  h.calls[1].resolve([]); await tick();
  assert.equal(nodes(h.render()).some((node) => node.props?.role === 'alert'), false);
});

test('session refresh bypasses cached identity after sign-in or sign-out', async () => {
  const calls = [], states = [];
  let cursor = 0;
  const react = { createContext: () => ({ Provider: 'provider' }), useCallback: (fn) => fn,
    useEffect() {}, useMemo: (fn) => fn(), useState(initial) {
      const i = cursor++;
      if (!(i in states)) states[i] = initial;
      return [states[i], (value) => { states[i] = value; }];
    } };
  const { AuthProvider } = compile('../src/auth/AuthProvider.tsx', {
    react, '../api/client': { api: async (path, options) => {
      calls.push({ path, options });
      return { id: calls.length };
    } },
  });
  const refresh = AuthProvider({}).props.value.refresh;
  await refresh(); await refresh();
  assert.deepEqual(calls.map((call) => call.path), ['/auth/me', '/auth/me']);
  assert.ok(calls.every((call) => call.options.cacheTtl === 0));
  cursor = 0;
  assert.equal(AuthProvider({}).props.value.user.id, 2);
});

for (const [path, name, props, field, next, loading, stale] of [
  ['search/StoryQuestionResults', 'StoryQuestionResults', { query: 'first', version: '', locale: 'en' },
    'query', 'second', 'Searching story notes…', { results: [] }],
  ['story/QuestExplanation', 'QuestExplanation', { questId: 1, version: '', locale: 'en' },
    'locale', 'ru', 'Loading story notes…', { explanation: null }],
]) {
  test(`${name} never replaces the current loading state with a late previous selection`, async () => {
    const h = harness(path, name, props);
    h.render(); props[field] = next; h.render();
    assert.equal(h.calls[0].options.signal.aborted, true);
    h.calls[0].resolve(stale); await tick();
    assert.ok(nodes(h.render()).some((node) => node.props?.children === loading));
  });
}
