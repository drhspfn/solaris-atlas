import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
import { activityDestination, dialogueSearchIndex, sidebarWindow } from '../src/components/quest/questSidebarModel.ts';
import { isSourceOnlyActivity, sidebarActivityTitle } from '../src/components/story/runtimeActivity.ts';

const entries = Array.from({ length: 75 }, (_, i) => ({
  id: `occurrence-${i}`, title: `Passage ${i}`, fallback: 'Dialogue', searchText: `Passage ${i}`,
  href: `#flow-state-${i}`, inView: true,
}));

test('75 loaded passages start at ten, expand by ten and keep repeated titles as separate occurrences', () => {
  assert.equal(sidebarWindow(entries, '').entries.length, 10);
  assert.equal(sidebarWindow(entries, '', 20).entries.length, 20);
  const repeated = [{ ...entries[0], title: 'Same' }, { ...entries[1], title: 'Same' }];
  assert.deepEqual(sidebarWindow(repeated, '').entries.map((row) => row.id), ['occurrence-0', 'occurrence-1']);
});

test('a late cited passage stays visible without expanding all75 rows; search remains local', () => {
  const page = sidebarWindow(entries, '', 10, '#flow-state-74');
  assert.equal(page.entries.length, 10);
  assert.equal(page.start, 65);
  assert.equal(page.entries.at(-1).href, '#flow-state-74');
  assert.equal(sidebarWindow(entries, '', 20, '#flow-state-74').entries.length, 20);
  assert.equal(sidebarWindow(entries, 'passage 74').entries[0].id, 'occurrence-74');
  assert.equal(sidebarWindow(entries, 'no match').entries.length, 0);
  assert.equal(entries.length, 75);
});

test('dialogue search finds SHUT UP in a later loaded line while retaining the first-line title', () => {
  const index = dialogueSearchIndex([
    { flow_state: 'cinematic', text: 'A different first line' },
    { flow_state: 'other', text: 'Elsewhere' },
    { flow_state: 'cinematic', text: 'SHUT UP!' },
    { flow_state: 'cinematic', text: 'THAT IS NOT HOME.' },
  ]);
  const rows = [{ ...entries[0], title: 'A different first line', searchText: index.get('cinematic') },
    { ...entries[1], searchText: index.get('other') }];
  assert.equal(sidebarWindow(rows, 'SHUT UP').entries[0].id, 'occurrence-0');
  assert.equal(sidebarWindow(rows, 'not home').entries[0].title, 'A different first line');
  assert.equal(sidebarWindow(rows, 'unloaded passage').total, 0);
});

test('mounted related/null-position activity keeps its exact anchor; absent targets never get dead hashes', () => {
  const related = { id: 'related:1', position: null, ordering: 'related' };
  assert.equal(activityDestination(related, new Map([['related:1', 'activity-exact']]), [], ''), '#activity-exact');
  assert.equal(activityDestination(related, new Map(), [], ''), undefined);
});

test('source-only generic activities are excluded without dropping meaningful generic contexts or merging occurrences', () => {
  const hidden = Array.from({ length: 62 }, (_, i) => ({ id: `source-${i}`,
    title: i % 2 ? 'Continue the story' : 'Story activity', flow_states: [], interactions: [] }));
  const meaningful = [
    { id: 'talk-a', title: 'Continue the story', condition_type: 'PlayFlow', flow_states: [{ state: 'a' }] },
    { id: 'talk-b', title: 'Continue the story', condition_type: 'PlayFlow', flow_states: [{ state: 'a' }] },
    { id: 'inspect', title: 'Story activity', interactions: [{ id: 'interaction' }] },
    { id: 'exit', title: 'Story activity', transition: { kind: 'dungeon_exit' } },
    { id: 'battle', title: 'Story activity', encounter: { trees: [] } },
    { id: 'named', title: 'Reach the station' },
  ];
  const source = [...hidden, ...meaningful];
  const visible = source.filter((block) => !isSourceOnlyActivity(block));
  assert.equal(source.length, 68);
  assert.deepEqual(visible.map((block) => block.id), meaningful.map((block) => block.id));
  assert.equal(visible.length, 6, 'public count excludes62 technical source-only rows');
  assert.equal(sidebarActivityTitle(visible[0]), 'Conversation');
  assert.equal(sidebarActivityTitle(visible[1]), 'Conversation');
  assert.equal(sidebarActivityTitle({ ...visible[0], title: 'Speak with Aemeath' }), 'Speak with Aemeath');
  assert.equal(activityDestination(hidden[0], new Map([['source-0', 'preserved-source-anchor']]), [], ''), '#preserved-source-anchor');
});

test('filtered or out-of-window activity uses an exact focus-line link retaining locale/version', () => {
  const block = { id: 'battle', encounter: { trees: [{ root: {
    children: [{ flow_states: [{ state: '剧情_3_28_1' }] }],
  } }] } };
  const href = activityDestination(block, new Map(), [{ id: 'talk_item:剧情_3_28_1:1:7', flow_state: '剧情_3_28_1' }], 'locale=zh&game_version=3.7.0&line=old');
  const params = new URLSearchParams(href.slice(1));
  assert.equal(params.get('line'), 'talk_item:剧情_3_28_1:1:7');
  assert.equal(params.get('locale'), 'zh');
  assert.equal(params.get('game_version'), '3.7.0');
  assert.equal(activityDestination(block, new Map(), [], 'locale=zh'), undefined);
});

const require = createRequire(import.meta.url);
function harness(groups, activeAnchor = '') {
  const states = [];
  const effects = [];
  let index = 0, effectIndex = 0, pending = [];
  let props = { groups, display: {}, activeAnchor };
  const exports = {};
  const code = ts.transpileModule(readFileSync(new URL('../src/components/quest/QuestSidebar.tsx', import.meta.url), 'utf8'), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
  }).outputText;
  const mocks = {
    react: { useEffect(run, deps) {
      const i = effectIndex++;
      if (!effects[i] || deps.some((value, j) => value !== effects[i][j])) {
        effects[i] = deps; pending.push(run);
      }
    }, useState(initial) {
      const i = index++;
      if (!(i in states)) states[i] = initial;
      return [states[i], (next) => { states[i] = typeof next === 'function' ? next(states[i]) : next; }];
    } },
    '../dialogue/PlayerText': { PlayerText: () => null },
    './questSidebarModel': { sidebarWindow },
    './QuestSidebar.module.css': { __esModule: true, default: new Proxy({}, { get: (_, key) => key }) },
  };
  vm.runInNewContext(code, { exports, require: (id) => mocks[id] ?? require(id), window: {
    location: { hash: '' }, addEventListener() {}, removeEventListener() {},
    matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }),
  } });
  return (changes = {}) => {
    props = { ...props, ...changes }; index = 0; effectIndex = 0; pending = [];
    const result = exports.QuestSidebar(props);
    for (const run of pending) run();
    return result;
  };
}
function all(node, type) {
  if (!node || typeof node !== 'object') return [];
  if (Array.isArray(node)) return node.flatMap((child) => all(child, type));
  return [...(node.type === type ? [node] : []), ...all(node.props?.children, type)];
}

test('actual grouped sidebar expands ten at a time, searches and reports empty results', () => {
  const render = harness([{ id: 'dialogue', title: 'Dialogue', entries }]);
  assert.equal(all(render(), 'a').length, 10);
  all(render(), 'button')[0].props.onClick();
  assert.equal(all(render(), 'a').length, 20);
  all(render(), 'input')[0].props.onChange({ target: { value: 'Passage 74' } });
  assert.equal(all(render(), 'a').length, 1);
  assert.equal(all(render(), 'a')[0].props.href, '#flow-state-74');
  all(render(), 'input')[0].props.onChange({ target: { value: 'not present' } });
  assert.equal(all(render(), 'a').length, 0);
  assert.equal(all(render(), 'button').length, 0);
});

test('parent playback rerenders preserve manual collapse; active anchor expands only a bounded context', () => {
  const groups = [{ id: 'dialogue', title: 'Dialogue', entries }];
  const render = harness(groups, 'flow-state-74');
  render();
  assert.equal(all(render(), 'a').length, 10);
  assert.equal(all(render(), 'a').at(-1).props.href, '#flow-state-74');
  all(render(), 'summary')[1].props.onClick({ preventDefault() {} });
  assert.equal(all(render(), 'details')[1].props.open, false);
  render({ groups: groups.map((group) => ({ ...group, entries: [...group.entries] })) });
  assert.equal(all(render(), 'details')[1].props.open, false);
  all(render(), 'button')[0].props.onClick();
  assert.equal(all(render(), 'a').length, 20);
  assert.equal(all(render(), 'a').at(-1).props.href, '#flow-state-74');
});

test('actual sidebar preserves three scene occurrences and does not link unavailable activities', () => {
  const scenes = entries.slice(0, 3).map((entry) => ({ ...entry, title: 'Same scene' }));
  const render = harness([{ id: 'scenes', title: 'Scenes', entries: scenes },
    { id: 'activities', title: 'Activities', entries: [{ ...entries[0], href: undefined, inView: false }] }]);
  assert.equal(all(render(), 'a').length, 3);
  assert.deepEqual(all(render(), 'a').map((node) => node.props.href), scenes.map((entry) => entry.href));
  const css = readFileSync(new URL('../src/components/quest/QuestSidebar.module.css', import.meta.url), 'utf8');
  assert.equal((css.match(/overflow-y:\s*auto/g) || []).length, 1);
  assert.match(css, /max-width: 760px/);
});
