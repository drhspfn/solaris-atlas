import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
const source = readFileSync(new URL('../src/components/characters/CharacterProfileVisual.tsx', import.meta.url), 'utf8');
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText;
function harness(extra = {}) {
  let view = 'portrait'; const exports = {};
  const components = new Proxy({}, { get: (_, name) => name });
  vm.runInNewContext(code, { exports, require: id => id === 'react' ? { useState: () => [view, value => { view = value; }] }
    : id === 'react/jsx-runtime' ? { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }) }
    : id === '@/api/client' ? { apiUrl: value => value } : components });
  const props = { characterKey: 'character:1211', name: 'Denia', portraitUrl: '/portrait.png', initialModel: { status: 'ready' }, ...extra };
  return { props, render: () => exports.CharacterProfileVisual(props) };
}
function nodes(node) { return !node || typeof node !== 'object' ? [] : [node, ...[node.props?.children].flat(Infinity).flatMap(nodes)]; }
test('one visual slot switches from gallery portrait to deferred 3D preview', () => {
  const h = harness(); let tree = h.render();
  assert(nodes(tree).some(node => node.type === 'CharacterMediaTrigger'));
  assert(!nodes(tree).some(node => node.type === 'CharacterModel'));
  nodes(tree).find(node => node.type === 'Tabs').props.onValueChange('model'); tree = h.render();
  assert(!nodes(tree).some(node => node.type === 'CharacterMediaTrigger'));
  const model = nodes(tree).find(node => node.type === 'CharacterModel');
  assert.equal(model.props.characterKey, 'character:1211');
  assert.equal(model.props.gameVersion, undefined);
});
test('missing models do not expose a dead switch; model-only characters still get a preview', () => {
  const missing = nodes(harness({ initialModel: { status: 'missing' } }).render());
  assert(!missing.some(node => node.type === 'Tabs' || node.type === 'CharacterModel'));
  assert(nodes(harness({ portraitUrl: null }).render()).some(node => node.type === 'CharacterModel'));
});
