import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';
import test from 'node:test';
import ts from 'typescript';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
const require = createRequire(import.meta.url);
function load(name, dependencies = {}) {
  const exports = {};
  const source = readFileSync(new URL(`../src/components/ui/${name}.tsx`, import.meta.url), 'utf8');
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  vm.runInNewContext(code, { exports, require: id => dependencies[id] ?? (id.includes('.css') ? {} : require(id)) });
  return exports;
}
const { Button } = load('Button');
const { Pagination } = load('Pagination', { './Button': { Button } });
const buttons = props => Pagination(props).props.children.filter(child => child.type === Button);

test('page navigation respects bounds, empty results, and reports a human-readable range', () => {
  const changes = [];
  const base = { mode: 'pages', page: 2, pageSize: 10, total: 23, onPageChange: value => changes.push(value) };
  const [previous, next] = buttons(base);
  previous.props.onClick(); next.props.onClick();
  assert.deepEqual(changes, [1, 3]);
  const first = buttons({ ...base, page: 1 }); first[0].props.onClick();
  const last = buttons({ ...base, page: 3 }); last[1].props.onClick();
  const empty = buttons({ ...base, page: 1, total: 0 }); empty.forEach(button => button.props.onClick());
  assert.equal(first[0].props.disabled, true); assert.equal(last[1].props.disabled, true);
  assert.ok(empty.every(button => button.props.disabled)); assert.deepEqual(changes, [1, 3]);
  const html = renderToStaticMarkup(React.createElement(Pagination, base));
  assert.match(html, /aria-label="Pagination"/); assert.match(html, /aria-live="polite"/);
  assert.match(html, /Page 2 of 3 · 11–20 of 23/);
  assert.match(renderToStaticMarkup(React.createElement(Pagination, { ...base, total: 0 })), /0 results/);
});
test('cursor navigation follows caller availability without inventing page numbers', () => {
  const calls = [];
  const props = { mode: 'cursor', hasPrevious: false, hasNext: true, onPrevious: () => calls.push('previous'), onNext: () => calls.push('next'), label: 'More results' };
  const [previous, next] = buttons(props); previous.props.onClick(); next.props.onClick();
  assert.deepEqual(calls, ['next']);
  const disabled = buttons({ ...props, disabled: true, hasPrevious: true });
  disabled.forEach(button => button.props.onClick()); assert.deepEqual(calls, ['next']);
  const html = renderToStaticMarkup(React.createElement(Pagination, props));
  assert.match(html, /More results/); assert.doesNotMatch(html, /Page \d/);
});
