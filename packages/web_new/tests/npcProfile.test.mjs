import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/components/profile/NpcProfile.tsx', import.meta.url), 'utf8');
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText;
const compile = path => ts.transpileModule(readFileSync(new URL(path, import.meta.url), 'utf8'),
  { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText;
const colors = {};
vm.runInNewContext(compile('../src/data/gameTextColors.ts'), { exports: colors });
const gameText = {};
vm.runInNewContext(compile('../src/data/gameText.ts'), { exports: gameText, require: () => colors });
const speakerHelpers = {};
vm.runInNewContext(ts.transpileModule(readFileSync(new URL('../src/data/npcContext.ts', import.meta.url), 'utf8'),
  { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText, { exports: speakerHelpers });
function nodes(node) { return !node || typeof node !== 'object' ? [] : [node, ...[node.props?.children].flat(Infinity).flatMap(nodes)]; }
function render(result, npc = { shop_count: 1 }, gameVersion) {
  let index = 0;
  const initial = ['', '', 1, result, '', 0];
  const exports = {};
  const components = new Proxy({}, { get: (_, name) => name });
  vm.runInNewContext(code, { exports, AbortController, URLSearchParams,
    require: (id) => id === 'react' ? { useState: () => [initial[index++], () => {}], useEffect: () => {}, useRef: () => ({ current: null }) }
      : id === 'react/jsx-runtime' ? { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }) }
      : id === '@/hooks/useLocale' ? { useLocale: () => 'en' }
      : id === '@/data/localized' ? { localizedText: (value, fallback = '') => value?.content || value || fallback }
      : id === '@/data/npcContext' ? speakerHelpers
      : id === '@/data/gameText' ? gameText
      : id === '@/lib/routeKey' ? { questPath: quest => `/quests/${quest.canonical_key}` }
      : components });
  return nodes(exports.NpcInteractions({ characterKey: 'character:npc:17288', npc, gameVersion }));
}
test('NPC profile labels interaction participants and bounds dialogue with pagination', () => {
  const tree = render({ total: 21, results: [{ canonical_key: 'talk_item:1', flow_state: 'flow',
    speaker: { canonical_key: 'speaker:9', label: 'Visitor' }, speaker_relation: 'interaction_participant',
    text: { content: 'Hello' }, quests: [] }] });
  const small = tree.filter((node) => node.type === 'small').flatMap((node) => node.props.children);
  assert(small.includes('Conversation participant'));
  const card = tree.find(node => node.props.className === 'npc-dialogue-card');
  const detail = nodes(card).find(node => node.type === 'details');
  assert.equal(detail.props.open, undefined);
  assert(nodes(detail).some(node => node.props.children?.includes?.('flow')));
  assert(!nodes(card).filter(node => node.type === 'small').some(node => String(node.props.children).includes('flow')));
  const pagination = tree.find((node) => node.type === 'Pagination');
  assert.equal(pagination.props.pageSize, 20);
  assert.equal(pagination.props.total, 21);
  assert(tree.some((node) => node.type === 'NpcShops'));
});

test('clearing NPC dialogue search resets its query and page and restores focus', () => {
  const slots = ['visitor', 'visitor', 3, { total: 0, results: [] }, '', 0];
  let index = 0; let focused = false;
  const exports = {};
  const components = new Proxy({}, { get: (_, name) => name });
  vm.runInNewContext(code, { exports, AbortController, URLSearchParams,
    require: (id) => id === 'react' ? { useState: () => { const i = index++; return [slots[i], (value) => { slots[i] = value; }]; }, useEffect: () => {}, useRef: () => ({ current: { focus: () => { focused = true; } } }) }
      : id === 'react/jsx-runtime' ? { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }) }
      : id === '@/hooks/useLocale' ? { useLocale: () => 'en' }
      : id === '@/data/localized' ? { localizedText: (value, fallback = '') => value?.content || value || fallback }
      : id === '@/data/npcContext' ? speakerHelpers
      : id === '@/data/gameText' ? gameText
      : components });
  const tree = nodes(exports.NpcInteractions({ characterKey: 'character:npc:1', npc: { shop_count: 0 } }));
  tree.find((node) => node.props['aria-label'] === 'Clear dialogue search').props.onClick();
  assert.equal(slots[0], '');
  assert.equal(slots[1], '');
  assert.equal(slots[2], 1);
  assert.equal(focused, true);
});

test('NPC profile delegates interactions to its canonical key', () => {
  const exports = {};
  const components = new Proxy({}, { get: (_, name) => name });
  vm.runInNewContext(code, { exports, AbortController, URLSearchParams,
    require: id => id === 'react' ? { useState: initial => [initial, () => {}], useEffect: () => {}, useRef: () => ({ current: null }) }
      : id === 'react/jsx-runtime' ? { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }) }
      : id === '@/hooks/useLocale' ? { useLocale: () => 'en' }
      : id === '@/data/localized' ? { localizedText: (value, fallback = '') => value?.content || value || fallback }
      : id === '@/data/npcContext' ? speakerHelpers
      : id === '@/data/gameText' ? gameText
      : components });
  const npc = { blueprint_id: 1, name_scope: 'blueprint', name_variants: [], shop_count: 1 };
  const tree = nodes(exports.NpcProfile({ data: { character: { canonical_key: 'character:npc:1' }, npc }, gameVersion: '3.7' }));
  const interactions = tree.find(node => node.type === exports.NpcInteractions);
  assert.equal(interactions.props.characterKey, 'character:npc:1');
  assert.equal(interactions.props.gameVersion, '3.7');
  assert.equal(interactions.props.npc, npc);
});

test('quest appearances render localized, accessible links in a compact grid', () => {
  const exports = {};
  const components = new Proxy({}, { get: (_, name) => name });
  let index = 0;
  const slots = ['', '', 1, { results: [], total: 0 }, '', 0];
  vm.runInNewContext(code, { exports, AbortController, URLSearchParams,
    require: id => id === 'react' ? { useState: () => [slots[index++], () => {}], useEffect: () => {}, useRef: () => ({ current: null }) }
      : id === 'react/jsx-runtime' ? { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }) }
      : id === '@/hooks/useLocale' ? { useLocale: () => 'en' }
      : id === '@/data/localized' ? { localizedText: (value, fallback = '') => value?.content || value || fallback }
      : id === '@/data/npcContext' ? speakerHelpers
      : id === '@/data/gameText' ? gameText
      : id === '@/lib/routeKey' ? { questPath: quest => `/quests/${quest.canonical_key}` }
      : components });
  const interactions = nodes(exports.NpcInteractions({ characterKey: 'character:npc:1', npc: { shop_count: 0 }, gameVersion: '3.7.1' }));
  const quests = interactions.find(node => typeof node.type === 'function' && node.type.name === 'NpcQuests');
  index = 0;
  slots.splice(0, slots.length, 1, { results: [], total: 0 }, '', 0);
  assert.equal(quests.type(quests.props), null);
  index = 0;
  slots.splice(0, slots.length, 1, { results: [{ canonical_key: 'quest:1', game_quest_id: 1, name: { content: 'A localized quest' } }], total: 1 }, '', 0);
  const rendered = nodes(quests.type(quests.props));
  const link = rendered.find(node => node.props.className === 'npc-quest-card');
  assert.equal(link.props.to, '/quests/quest:1?locale=en&game_version=3.7.1');
  assert.equal(link.props.children, 'A localized quest');
  assert(rendered.some(node => node.props.className === 'npc-quest-grid'));
});

test('conversation lookup runs even without placed flow references', () => {
  const tree = render({ total: 0, results: [] }, { shop_count: 0, flow_reference_count: 0 }, '3.7.1');
  const conversations = tree.find(node => node.type === 'NpcFlowParticipants');
  assert.equal(conversations.props.characterKey, 'character:npc:17288');
  assert.equal(conversations.props.gameVersion, '3.7.1');
});

test('dialogue quest links preserve locale and game version', () => {
  const tree = render({ total: 1, results: [{ canonical_key: 'talk_item:1', flow_state: 'flow',
    speaker: null, speaker_relation: 'interaction_participant', text: { content: 'Hello' },
    quests: [{ canonical_key: 'quest:7', game_quest_id: 7, name: { content: 'Quest' } }] }] },
  { shop_count: 0, flow_reference_count: 0 }, '3.7.1');
  const link = tree.find(node => node.props.to?.startsWith('/quests/quest:7'));
  const query = new URLSearchParams(link.props.to.split('?')[1]);
  assert.equal(query.get('locale'), 'en');
  assert.equal(query.get('game_version'), '3.7.1');
});

test('dialogue cards use the shared speaker display names', () => {
  for (const [label, expected] of [['{PlayerName}', 'Rover'], ['speaker:123', 'Unknown speaker'],
    ['???', 'Unknown speaker'], ['{message}Visitor', 'Visitor']]) {
    const tree = render({ total: 1, results: [{ canonical_key: 'talk_item:1', flow_state: 'flow',
      speaker: { canonical_key: 'speaker:123', label }, speaker_relation: 'interaction_participant',
      text: { content: 'Hello' }, quests: [] }] });
    const card = tree.find(node => node.props.className === 'npc-dialogue-meta');
    assert.equal(nodes(card).find(node => node.type === 'span').props.children, expected);
  }
});

test('NPC dialogue renders supported game colors as text spans', () => {
  const tree = render({ total: 1, results: [{ canonical_key: 'talk_item:1', flow_state: 'flow',
    speaker: null, speaker_relation: 'interaction_participant',
    text: { content: 'Hello <color=Highlight>Rover</color> <unknown>tag</unknown>' }, quests: [] }] });
  const text = tree.find(node => node.props.className === 'npc-dialogue-text');
  const spans = nodes(text).filter(node => node.type === 'span');
  assert(spans.some(node => node.props.children === 'Rover' && node.props.style?.color === '#F7EBA6FF'));
  assert(spans.some(node => node.props.children.includes('<unknown>tag</unknown>')));
  assert(!JSON.stringify(text).includes('<color='));
});
