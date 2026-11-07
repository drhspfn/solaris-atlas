import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/components/cards/EntityCard.tsx', import.meta.url), 'utf8');
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText;
function nodes(node) { return !node || typeof node !== 'object' ? [] : [node, ...[node.props?.children].flat(Infinity).flatMap(nodes)]; }
test('catalog card passes authored title and parent display to PlayerText', () => {
  const components = new Proxy({}, { get: (_, name) => name });
  const display = { name: 'Rover', gender: 'female', colorMode: 'game', customColor: '' };
  const mocks = {
    '../../data/entities': { display: (item) => item.title || item.alias || item.canonical_key, entityPath: () => '/item' },
    '../../config/settings': { APP_SETTINGS: { presentation: { cardStaggerMaxIndex: 5, cardStaggerMs: 10 } } },
    'react/jsx-runtime': { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }) },
  };
  const exports = {};
  vm.runInNewContext(code, { exports, require: (id) => mocks[id] ?? components });
  const tree = nodes(exports.EntityCard({ item: { canonical_key: 'item:1', node_type: 'item', title: 'Hello {PlayerName}' }, display }));
  const title = tree.find((node) => node.type === 'PlayerText');
  assert.equal(title.props.value, 'Hello {PlayerName}');
  assert.equal(title.props.display, display);
});
