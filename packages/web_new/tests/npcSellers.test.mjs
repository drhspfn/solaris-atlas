import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/components/items/NpcSellers.tsx', import.meta.url), 'utf8');
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText;
function nodes(node) { return !node || typeof node !== 'object' ? [] : [node, ...[node.props?.children].flat(Infinity).flatMap(nodes)]; }
test('seller loading uses row skeletons and an empty result hides the section', () => {
  const exports = {}, components = new Proxy({}, { get: (_, name) => name });
  let slots = [1, null, '', 0], index = 0;
  vm.runInNewContext(code, { exports, AbortController, URLSearchParams,
    require: id => id === 'react' ? { useState: () => [slots[index++], () => {}], useEffect: () => {} }
      : id === 'react/jsx-runtime' ? { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }) } : components });
  const loading = nodes(exports.NpcSellers({ itemId: 100, locale: 'en' })).find(node => node.type === 'SkeletonGroup');
  assert.equal(loading.props.label, 'Loading shopkeepers');
  assert(loading.props.children.every(node => node.type.name === 'NpcSellerSkeleton'));
  slots = [1, { total: 0, results: [] }, '', 0]; index = 0;
  assert.equal(exports.NpcSellers({ itemId: 100, locale: 'en' }), null);
});
test('NPC sellers show scoped names and paginate results', () => {
  const result = { total: 21, results: [{ canonical_key: 'character:npc:17290', href: '/characters/zahira', name: 'Zahira', offers: [{ shop_id: 1 }] }] };
  const initial = [1, result, '', 0]; let index = 0; const exports = {};
  const components = new Proxy({}, { get: (_, name) => name });
  vm.runInNewContext(code, { exports, AbortController, URLSearchParams,
    require: (id) => id === 'react' ? { useState: () => [initial[index++], () => {}], useEffect: () => {} }
      : id === 'react/jsx-runtime' ? { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }) } : components });
  const display = { name: 'Rover', colorMode: 'game', customColor: '' };
  const tree = nodes(exports.NpcSellers({ itemId: 100, locale: 'en', display }));
  const link = tree.find((node) => node.type === 'Link');
  assert.equal(link.props.to, '/characters/zahira');
  const name = tree.find((node) => node.type === 'PlayerText');
  assert.equal(name.props.value, 'Zahira');
  assert.equal(name.props.display, display);
  assert.equal(tree.find((node) => node.type === 'Pagination').props.total, 21);
});

test('seller requests use the selected locale and page and abort on cleanup', () => {
  const initial = [2, null, '', 0]; let index = 0; let effect; let request;
  const exports = {};
  const components = new Proxy({}, { get: (_, name) => name });
  vm.runInNewContext(code, { exports, AbortController, URLSearchParams,
    require: (id) => id === 'react' ? { useState: () => [initial[index++], () => {}], useEffect: (fn) => { effect = fn; } }
      : id === '@/api/client' ? { api: (url, options) => { request = { url, ...options }; return new Promise(() => {}); } }
      : id === 'react/jsx-runtime' ? { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }) } : components });
  exports.NpcSellers({ itemId: 100, locale: 'ru' });
  const cleanup = effect();
  const url = new URL(request.url, 'https://atlas.test');
  assert.equal(url.pathname, '/item-npc-sellers/100');
  assert.equal(url.searchParams.get('locale'), 'ru');
  assert.equal(url.searchParams.get('limit'), '20');
  assert.equal(url.searchParams.get('offset'), '20');
  cleanup();
  assert.equal(request.signal.aborted, true);
});

test('item profile mounts sellers for its game item id', () => {
  const profile = readFileSync(new URL('../src/components/profile/EntityProfileClient.tsx', import.meta.url), 'utf8');
  const compiled = ts.transpileModule(profile, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  const exports = {};
  const components = new Proxy({}, { get: (_, name) => name });
  const mocks = {
    react: { useState: (value) => [value, () => {}], useEffect: () => {} },
    '@/hooks/useLocale': { useLocale: () => 'ru' },
    '@/hooks/usePlayerDisplay': { usePlayerDisplay: () => ({}) },
    '@/data/localized': { localizedText: (value, fallback = '') => value || fallback },
    '@/data/entities': { categoryTitle: { item: 'Items' } },
    '@/config/settings': { APP_SETTINGS: { limits: { relatedPreview: 5 } } },
  };
  vm.runInNewContext(compiled, { exports, require: (id) => mocks[id] ?? (id === 'react/jsx-runtime'
    ? { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }) } : components) });
  const tree = nodes(exports.EntityProfileClient({ kind: 'item', entityKey: 'item:100', initialData: { item: { game_item_id: 100, canonical_key: 'item:100' } } }));
  const sellers = tree.find((node) => node.type === 'NpcSellers');
  assert.equal(sellers.props.itemId, 100);
  assert.equal(sellers.props.locale, 'ru');
});
