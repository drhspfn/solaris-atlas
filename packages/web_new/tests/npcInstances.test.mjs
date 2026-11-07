import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/components/profile/NpcInstances.tsx', import.meta.url), 'utf8');
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText;
function nodes(node) { return !node || typeof node !== 'object' ? [] : [node, ...[node.props?.children].flat(Infinity).flatMap(nodes)]; }

test('instance list fetches versioned server pages and keeps map/entity distinctions', async () => {
  const slots = [], effects = [], requests = [];
  let index = 0, effectIndex = 0, pending = [];
  const instances = Array.from({ length: 21 }, (_, i) => ({ map_id: 10, entity_id: i + 1,
    name: { content: `Instance ${i + 1}` }, character_key: `character:npc:${i + 1}` }));
  const exports = {};
  vm.runInNewContext(code, { exports, URLSearchParams, AbortController, require: id => {
    if (id === 'react') return { useState: initial => { const i = index++; if (!(i in slots)) slots[i] = initial; return [slots[i], value => { slots[i] = value; }]; },
      useEffect: (run, deps) => { const i = effectIndex++; if (!effects[i] || deps.some((v, j) => v !== effects[i].deps[j])) pending.push(() => { effects[i]?.cleanup?.(); effects[i] = { deps, cleanup: run() }; }); } };
    if (id === 'react/jsx-runtime') return { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }) };
    if (id === '@/hooks/useLocale') return { useLocale: () => 'en' };
    if (id === '@/api/client') return { api: async url => { requests.push(url); const offset = Number(new URL(url, 'https://example.test').searchParams.get('offset')); return { results: instances.slice(offset, offset + 20), total: instances.length }; } };
    if (id === '@/components/ui/Pagination') return { Pagination: 'Pagination' };
    return { localizedText: (value, fallback) => value?.content || value || fallback };
  } });
  function render() { index = effectIndex = 0; pending = []; const tree = exports.NpcInstances({ characterKey: 'character:npc:1', gameVersion: '3.7' }); for (const run of pending) run(); return nodes(tree); }
  assert(render().some(node => node.props.role === 'status'));
  await Promise.resolve(); await Promise.resolve();
  let tree = render();
  assert.equal(tree.filter(node => node.props.className === 'plain-row').length, 20);
  tree.find(node => node.type === 'Pagination').props.onPageChange(2);
  render();
  await Promise.resolve(); await Promise.resolve();
  tree = render();
  assert.equal(tree.filter(node => node.props.className === 'plain-row').length, 1);
  assert(tree.some(node => node.type === 'span' && node.props.children === 'Instance 21'));
  assert.deepEqual(Array.from(tree.find(node => node.type === 'small').props.children), ['Map ', 10, ' · Entity ', 21]);
  const params = new URL(requests[1], 'https://example.test').searchParams;
  assert.equal(params.get('offset'), '20');
  assert.equal(params.get('game_version'), '3.7');
  assert.equal(params.get('locale'), 'en');
});
