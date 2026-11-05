import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';
import test from 'node:test';
import ts from 'typescript';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
const require = createRequire(import.meta.url);
function load(name) {
  const exports = {};
  const source = readFileSync(new URL(`../src/components/ui/${name}.tsx`, import.meta.url), 'utf8');
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  vm.runInNewContext(code, { exports, require: id => id.includes('.css') ? {} : require(id) });
  return exports[name];
}
const Button = load('Button'), Input = load('Input'), Checkbox = load('Checkbox'), Slider = load('Slider');
const render = (component, props, child) => renderToStaticMarkup(React.createElement(component, props, child));
test('loading buttons prevent duplicate actions while retaining an accessible label', () => {
  const html = render(Button, { loading: true, loadingLabel: 'Loading model' }, 'Load model');
  for (const expected of ['type="button"', 'disabled=""', 'aria-busy="true"', 'Loading model', 'ui-button-spinner']) assert(html.includes(expected));
});
test('left/right and icon-only buttons retain explicit accessible names', () => {
  const icon = React.createElement('svg');
  const left = render(Button, { icon }, 'Settings'), right = render(Button, { icon, iconPosition: 'right' }, 'Settings');
  assert(left.indexOf('<svg') < left.indexOf('>Settings<'));
  assert(right.indexOf('<svg') > right.indexOf('>Settings<'));
  const only = render(Button, { icon, size: 'icon', 'aria-label': 'Model settings' });
  assert.match(only, /aria-label="Model settings"/); assert.match(only, /ui-button-icon-only/); assert.doesNotMatch(only, /ui-button-label/);
});
test('native field atoms preserve form names, bounds, disabled state and label semantics', () => {
  const input = render(Input, { name: 'offset', type: 'number', min: -10, max: 10, required: true, 'aria-label': 'Target X' });
  assert.match(input, /type="number"/); assert.match(input, /min="-10"/); assert.match(input, /required=""/);
  const checkbox = render(Checkbox, { label: 'Follow bone', checked: true, disabled: true, readOnly: true });
  assert.match(checkbox, /<label/); assert.match(checkbox, /type="checkbox"/); assert.match(checkbox, /checked=""/); assert.match(checkbox, /Follow bone/);
  const slider = render(Slider, { label: 'Animation position', value: 4, min: 0, max: 10, step: .01, readOnly: true });
  assert.match(slider, /type="range"/); assert.match(slider, /value="4"/); assert.match(slider, /Animation position/);
});
