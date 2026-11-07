import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/components/items/ItemAcquisition.tsx', import.meta.url), 'utf8');
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText;
function nodes(node) { return !node || typeof node !== 'object' ? [] : [node, ...[node.props?.children].flat(Infinity).flatMap(nodes)]; }

test('acquisition paths compare plain names and pass display to source text', () => {
  const display = { name: 'Rover', colorMode: 'game', customColor: '' };
  const components = new Proxy({}, { get: (_, name) => name });
  const exports = {};
  vm.runInNewContext(code, { exports, require: (id) => id === 'react/jsx-runtime'
    ? { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }) }
    : id === '../../data/gameText' ? { gameTextPlainText: (value) => (value?.content ?? '').replace(/<[^>]+>/g, '') }
    : components });
  const data = {
    shop_offers: [{ offer_id: 1, shop_id: 3, shop_name: { content: '<color=red>Store</color>' }, item_count: 1 }],
    acquisition_paths: [{ id: 1, description: { content: 'Store' } }, { id: 2, description: { content: 'Ask {PlayerName}' } }],
  };
  const tree = nodes(exports.ItemAcquisition({ data, locale: 'en', display }));
  const text = tree.filter((node) => node.type === 'PlayerText');
  assert(text.some((node) => node.props.value?.content === 'Ask {PlayerName}' && node.props.display === display));
  assert(!text.some((node) => node.props.value?.content === 'Store'));
});
