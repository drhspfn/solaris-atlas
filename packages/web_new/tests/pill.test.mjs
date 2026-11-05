import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';
import test from 'node:test';
import ts from 'typescript';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

const require = createRequire(import.meta.url);
const source = readFileSync(new URL('../src/components/ui/Pill.tsx', import.meta.url), 'utf8');
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText;
const exports = {};
vm.runInNewContext(code, { exports, require: id => id.includes('.css') ? {} : id === '@/lib/router' ? { Link: ({ children, ...props }) => React.createElement('a', props, children) } : require(id) });
const render = props => renderToStaticMarkup(React.createElement(exports.Pill, props, 'Character'));

test('informational pills are not interactive', () => {
  assert.match(render({}), /^<span class="ui-pill /);
});
test('filter buttons expose selected and disabled states', () => {
  const selected = render({ kind: 'button', selected: true });
  assert.match(selected, /^<button/);
  assert.match(selected, /type="button"/);
  assert.match(selected, /aria-pressed="true"/);
  const disabled = render({ kind: 'button', disabled: true });
  assert.match(disabled, /disabled=""/);
  assert.match(disabled, /aria-pressed="false"/);
});
test('selected links expose their destination and current page', () => {
  const html = render({ kind: 'link', href: '/characters', selected: true });
  assert.match(html, /^<a/);
  assert.match(html, /href="\/characters"/);
  assert.match(html, /aria-current="page"/);
  assert.doesNotMatch(render({ kind: 'link', href: '/characters' }), /aria-current/);
});
