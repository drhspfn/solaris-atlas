import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/components/profile/NpcProfile.tsx', import.meta.url), 'utf8');
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText;
function nodes(node) { return !node || typeof node !== 'object' ? [] : [node, ...[node.props?.children].flat(Infinity).flatMap(nodes)]; }
function render(result) {
  let index = 0;
  const initial = [0, '', '', 1, result, '', 0];
  const exports = {};
  const components = new Proxy({}, { get: (_, name) => name });
  vm.runInNewContext(code, { exports, AbortController, URLSearchParams,
    require: (id) => id === 'react' ? { useState: () => [initial[index++], () => {}], useEffect: () => {}, useRef: () => ({ current: null }) }
      : id === 'react/jsx-runtime' ? { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }) }
      : id === '@/hooks/useLocale' ? { useLocale: () => 'en' }
      : id === '@/data/localized' ? { localizedText: (value, fallback = '') => value?.content || value || fallback }
      : components });
  return nodes(exports.NpcProfile({ data: { character: { canonical_key: 'character:npc:17288', name: { content: 'I.R.I.S.' } },
    npc: { blueprint_id: 17288, name_scope: 'blueprint', name_variants: [], shop_count: 1, flow_reference_count: 1 } } }));
}
test('NPC profile labels interaction participants and bounds dialogue with pagination', () => {
  const tree = render({ total: 21, results: [{ canonical_key: 'talk_item:1', flow_state: 'flow',
    speaker: { canonical_key: 'speaker:9', label: 'Visitor' }, speaker_relation: 'interaction_participant',
    text: { content: 'Hello' }, quests: [] }] });
  const small = tree.filter((node) => node.type === 'small').flatMap((node) => node.props.children);
  assert(small.includes('Interaction participant'));
  const pagination = tree.find((node) => node.type === 'Pagination');
  assert.equal(pagination.props.pageSize, 20);
  assert.equal(pagination.props.total, 21);
  assert(tree.some((node) => node.type === 'NpcShops'));
});

test('clearing NPC dialogue search resets its query and page and restores focus', () => {
  const slots = [0, 'visitor', 'visitor', 3, { total: 0, results: [] }, '', 0];
  let index = 0; let focused = false;
  const exports = {};
  const components = new Proxy({}, { get: (_, name) => name });
  vm.runInNewContext(code, { exports, AbortController, URLSearchParams,
    require: (id) => id === 'react' ? { useState: () => { const i = index++; return [slots[i], (value) => { slots[i] = value; }]; }, useEffect: () => {}, useRef: () => ({ current: { focus: () => { focused = true; } } }) }
      : id === 'react/jsx-runtime' ? { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }) }
      : id === '@/hooks/useLocale' ? { useLocale: () => 'en' }
      : id === '@/data/localized' ? { localizedText: (value, fallback = '') => value?.content || value || fallback }
      : components });
  const tree = nodes(exports.NpcProfile({ data: { character: { canonical_key: 'character:npc:1' }, npc: { blueprint_id: 1, name_scope: 'blueprint', name_variants: [], shop_count: 0 } } }));
  tree.find((node) => node.props['aria-label'] === 'Clear dialogue search').props.onClick();
  assert.equal(slots[1], '');
  assert.equal(slots[2], '');
  assert.equal(slots[3], 1);
  assert.equal(focused, true);
});
