import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const code = ts.transpileModule(readFileSync(new URL('../src/components/profile/NpcShops.tsx', import.meta.url), 'utf8'),
  { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText;
function nodes(node) { return !node || typeof node !== 'object' ? [] : [node, ...[node.props?.children].flat(Infinity).flatMap(nodes)]; }

test('shop loading uses shop skeletons', () => {
  const slots = [1, null, '', 0]; let index = 0; const exports = {};
  const components = new Proxy({}, { get: (_, name) => name });
  vm.runInNewContext(code, { exports, AbortController, URLSearchParams,
    require: id => id === 'react' ? { useState: () => [slots[index++], () => {}], useEffect: () => {} }
      : id === 'react/jsx-runtime' ? { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }) }
      : id === '@/hooks/useLocale' ? { useLocale: () => 'en' } : components });
  const loading = nodes(exports.NpcShops({ characterKey: 'npc:1', display: {} })).find(node => node.type === 'SkeletonGroup');
  assert.equal(loading.props.label, 'Loading shops');
  assert(loading.props.children.every(node => node.type.name === 'NpcShopSkeleton'));
});

test('shop, offer and price labels pass game text and display preferences to PlayerText', () => {
  const display = { name: 'Astra', colorMode: 'accent', customColor: '', gender: 'female' };
  const slots = [1, { total: 1, results: [{ id: 5, name: '<color=Highlight>{PlayerName}</color>', offer_count: 1 }] }, '', 0];
  let index = 0;
  const exports = {};
  const components = new Proxy({}, { get: (_, name) => name });
  vm.runInNewContext(code, { exports, AbortController, URLSearchParams,
    require: id => id === 'react' ? { useState: () => [slots[index++], () => {}], useEffect: () => {} }
      : id === 'react/jsx-runtime' ? { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }) }
      : id === '@/hooks/useLocale' ? { useLocale: () => 'en' } : components });
  const shop = nodes(exports.NpcShops({ characterKey: 'character:npc:1', display }))
    .find(node => typeof node.type === 'function' && node.type.name === 'ShopOffers');
  assert.equal(shop.props.display, display);
  index = 0;
  slots.splice(0, slots.length, true, 1, { total: 1, results: [{ id: 1, item: { label: '<b>Flower</b>' }, count: 1,
    price: [{ amount: 2, item: { label: '{PlayerName} token' } }], original_price: [], conditions: {} }] }, '', 0);
  const tree = nodes(shop.type(shop.props));
  const title = tree.find(node => node.type === 'PlayerText');
  assert.equal(title.props.value, '<color=Highlight>{PlayerName}</color>');
  assert.equal(title.props.display, display);
  const items = tree.filter(node => typeof node.type === 'function' && node.type.name === 'ItemLabel');
  const labels = items.map(item => nodes(item.type(item.props)).find(node => node.type === 'PlayerText'));
  assert.deepEqual(labels.map(label => label.props.value), ['<b>Flower</b>', '{PlayerName} token']);
  assert(labels.every(label => label.props.display === display));
});
