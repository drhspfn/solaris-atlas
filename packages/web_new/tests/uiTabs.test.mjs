import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';
import test from 'node:test';
import ts from 'typescript';
const require = createRequire(import.meta.url);
const source = readFileSync(new URL('../src/components/ui/Tabs.tsx', import.meta.url), 'utf8');
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText;
function harness(extra = {}) {
  const exports = {}, ref = { current: {} }, selected = [], focused = [];
  vm.runInNewContext(code, { exports, require: id => id.includes('.css') ? {} : id === 'react'
    ? { useId: () => 'tabs', useRef: () => ref } : require(id) });
  const props = { label: 'Viewer settings', value: 'scene', onChange: value => selected.push(value),
    items: [{ value: 'scene', label: 'Scene', panelId: 'scene-panel' },
      { value: 'unavailable', label: 'Unavailable', disabled: true }, { value: 'camera', label: 'Camera' }], ...extra };
  const render = () => {
    const tree = exports.Tabs(props);
    for (const tab of tree.props.children) tab.props.ref({ focus: () => focused.push(tab.key) });
    return tree;
  };
  return { props, render, selected, focused };
}
function key(tab, value) {
  let prevented = false;
  tab.props.onKeyDown({ key: value, defaultPrevented: false, preventDefault() { prevented = true; } });
  return prevented;
}
test('tabs expose controlled selection, optional panel links and each visual variant', () => {
  for (const variant of ['buttons', 'segmented', 'underline']) {
    const h = harness({ variant }); let tree = h.render();
    assert.equal(tree.props.role, 'tablist'); assert.equal(tree.props['aria-label'], 'Viewer settings');
    assert(tree.props.className.includes(`ui-tabs-${variant}`));
    assert.equal(tree.props.children[0].props['aria-controls'], 'scene-panel');
    assert.equal(tree.props.children[0].props['aria-selected'], true);
    tree.props.children[2].props.onClick(); assert.deepEqual(h.selected, ['camera']);
    h.props.value = 'camera'; tree = h.render();
    assert.equal(tree.props.children[2].props.tabIndex, 0);
    assert.equal(tree.props.children[0].props.tabIndex, -1);
  }
});
test('keyboard skips disabled tabs, wraps and moves actual focus with Home and End', () => {
  const h = harness(), tabs = h.render().props.children;
  assert(key(tabs[0], 'ArrowRight')); assert.deepEqual(h.selected, ['camera']); assert.deepEqual(h.focused, ['camera']);
  key(tabs[2], 'ArrowRight'); key(tabs[0], 'ArrowLeft'); key(tabs[2], 'Home'); key(tabs[0], 'End');
  assert.deepEqual(h.selected, ['camera', 'scene', 'camera', 'scene', 'camera']);
  assert.equal(key(tabs[0], 'a'), false);
});
test('vertical tabs use up/down and all-disabled sets do not consume keyboard input', () => {
  const h = harness({ orientation: 'vertical' }), tabs = h.render().props.children;
  assert.equal(key(tabs[0], 'ArrowRight'), false); assert(key(tabs[0], 'ArrowDown'));
  assert.deepEqual(h.selected, ['camera']);
  const disabled = harness({ items: [{ value: 'scene', label: 'Scene', disabled: true }] });
  assert.equal(key(disabled.render().props.children[0], 'Home'), false);
  assert.deepEqual(disabled.selected, []);
});
