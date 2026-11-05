import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';
import React from 'react';
import ts from 'typescript';
import { tooltipPosition } from '../src/components/ui/tooltipPosition.ts';
const require = createRequire(import.meta.url);
const code = ts.transpileModule(readFileSync(new URL('../src/components/ui/Tooltip.tsx', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
const rect = { left: 100, right: 150, top: 100, bottom: 130, width: 50, height: 30 };
const size = { width: 80, height: 20 }, viewport = { width: 500, height: 300 };
test('tooltip supports sides and alignment, flips near edges and remains within the viewport', () => {
  assert.deepEqual(tooltipPosition(rect, size, viewport, 'top', 'center', 8), { left: 85, top: 72 });
  assert.deepEqual(tooltipPosition(rect, size, viewport, 'right', 'end', 8), { left: 158, top: 110 });
  assert.deepEqual(tooltipPosition(rect, size, viewport, 'left', 'start', 8), { left: 12, top: 100 });
  assert.deepEqual(tooltipPosition({ ...rect, top: 2, bottom: 32 }, size, viewport, 'top', 'center', 8), { left: 85, top: 40 });
  assert.equal(tooltipPosition({ ...rect, left: 490, right: 540 }, size, viewport, 'bottom', 'start', 8).left, 412);
});
function harness(extra = {}) {
  let state = false, ri = 0, ei = 0, serial = 0, shown = 0;
  const refs = [], effects = [], pending = [], timers = new Map(), listeners = new Map(), exports = {};
  const hooks = { ...React, useId: () => 'help', useRef: initial => refs[ri++] ?? (refs[ri - 1] = { current: initial }),
    useState: () => [state, value => { state = value; }],
    useEffect: (fn, deps) => { const index = ei++, previous = effects[index];
      if (!previous || deps.some((dep, i) => dep !== previous.deps[i])) {
        pending.push(() => { previous?.cleanup?.(); effects[index] = { deps, cleanup: fn() }; });
      }
    } };
  vm.runInNewContext(code, { exports, require: id => id.includes('.css') ? {} : id === 'react' ? hooks
    : id === './tooltipPosition' ? { tooltipPosition } : require(id),
    setTimeout: fn => { timers.set(++serial, fn); return serial; }, clearTimeout: id => timers.delete(id),
    window: { innerWidth: 500, innerHeight: 300, addEventListener() {}, removeEventListener() {} },
    document: { addEventListener: (name, fn) => listeners.set(name, fn), removeEventListener: name => listeners.delete(name) } });
  const props = { children: React.createElement('button', { 'aria-describedby': 'existing' }, 'Control'), content: 'Help', ...extra };
  function render() {
    ri = ei = 0;
    const tree = exports.Tooltip(props);
    refs[0].current = { getBoundingClientRect: () => rect, contains: () => false };
    refs[1].current ??= { style: {}, getBoundingClientRect: () => size, showPopover() { shown++; }, hidePopover() {} };
    while (pending.length) pending.shift()();
    return tree;
  }
  return { props, render, timers, listeners, helpLabel: exports.HelpLabel, get shown() { return shown; },
    flush() { const callbacks = [...timers.values()]; timers.clear(); callbacks.forEach(fn => fn()); },
    unmount() { effects.forEach(effect => effect.cleanup?.()); } };
}
test('focus opens an accessible tooltip and Escape dismisses only the tooltip', () => {
  const h = harness();
  h.render().props.onFocusCapture(); h.flush();
  const tree = h.render();
  assert.equal(tree.props.children[0].props['aria-describedby'], 'existing help');
  assert.equal(tree.props.children[1].props.role, 'tooltip');
  assert.equal(h.shown, 1);
  let stopped = false;
  h.listeners.get('keydown')({ key: 'Escape', stopPropagation() { stopped = true; } });
  assert(stopped);
  assert.equal(h.render().props.children[1].props.hidden, true);
  assert.equal(h.listeners.size, 0);
  h.unmount();
});
test('disabled tooltips do not open and unmount cancels pending hover work', () => {
  const disabled = harness({ disabled: true });
  disabled.render().props.onPointerEnter(); assert.equal(disabled.timers.size, 0); disabled.unmount();
  const h = harness(); h.render().props.onPointerEnter(); assert.equal(h.timers.size, 1);
  h.unmount(); assert.equal(h.timers.size, 0);
});

test('help labels wrap the entire setting text and icon with a deliberate hover delay', () => {
  const h = harness();
  const help = h.helpLabel({ label: 'Spring-bone physics', content: 'Secondary motion' });
  assert.equal(help.props.delay, 600);
  assert.equal(help.props.children.props.className, 'ui-help-label');
  assert.equal(help.props.children.props.children[0], 'Spring-bone physics');
  assert.equal(help.props.children.props.children[1].props['aria-label'], 'About Spring-bone physics');
});
