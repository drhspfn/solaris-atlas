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
  const source = readFileSync(new URL(`../src/components/ui/${name}`, import.meta.url), 'utf8');
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  vm.runInNewContext(code, { exports, URL, require: id => dependencies[id] ?? (id.includes('.css') ? {} : require(id)) });
  return exports;
}
const helpers = load('mediaViewerUtils.ts');
const button = load('Button.tsx');
const { MediaViewer } = load('MediaViewer.tsx', { './Button': button, './mediaViewerUtils': helpers });
const render = (items, activeIndex) => renderToStaticMarkup(React.createElement(MediaViewer, { items, activeIndex, onIndexChange() {}, onClose() {} }));

test('navigation and zoom remain within their supported bounds', () => {
  assert.equal(helpers.mediaViewerIndex(0, -1, 3), 0);
  assert.equal(helpers.mediaViewerIndex(1, 1, 3), 2);
  assert.equal(helpers.mediaViewerIndex(2, 1, 3), 2);
  assert.equal(helpers.mediaViewerZoom(0), 1);
  assert.equal(helpers.mediaViewerZoom(5), 4);
});
test('media links accept web and relative URLs but reject unsafe schemes', () => {
  assert.equal(helpers.mediaViewerUrl('/art.png'), '/art.png');
  assert.equal(helpers.mediaViewerUrl('https://cdn.example/art.png'), 'https://cdn.example/art.png');
  assert.equal(helpers.mediaViewerUrl('javascript:alert(1)'), undefined);
  assert.equal(helpers.mediaViewerUrl('data:text/html,hello'), undefined);
});
test('image viewer exposes named controls and bounded navigation', () => {
  const html = render([{ id: 'a', src: '/a.png', type: 'image', title: 'Artwork' }], 0);
  for (const value of ['aria-label="Media viewer"', 'aria-label="Zoom in"', 'aria-label="Zoom out"', 'aria-label="Previous media"', 'aria-label="Next media"', 'aria-label="Download Artwork"', 'alt="Artwork"']) assert.ok(html.includes(value), value);
  assert.match(html, /aria-label="Previous media"[^>]*disabled/);
  assert.match(html, /aria-label="Next media"[^>]*disabled/);
});
test('video uses native controls without autoplay and invalid media exposes an error', () => {
  const html = render([{ id: 'v', src: '/video.mp4', type: 'video', title: 'Clip' }], 0);
  assert.match(html, /<video[^>]*controls/);
  assert.doesNotMatch(html, /autoplay/i);
  assert.doesNotMatch(html, /Zoom in/);
  const invalid = render([{ id: 'bad', src: 'javascript:alert(1)', type: 'image', title: 'Bad' }], 0);
  assert.match(invalid, /Media could not be loaded/);
  assert.doesNotMatch(invalid, /href="javascript:/);
});


// Exercise actual component event handlers and effects with a small hook host; the
// repository has no browser DOM test dependency.
function viewerHost(initialItems) {
  let slots = [], cursor = 0, pending = [], tree, props;
  const calls = { indices: [], closed: 0, focused: 0, shown: 0, hidden: 0 };
  class Element { focus() { calls.focused++; } }
  const opener = new Element();
  const document = { activeElement: opener, body: { style: { overflow: 'auto' } } };
  const dialog = { open: false, showModal() { this.open = true; calls.shown++; }, close() { this.open = false; calls.hidden++; } };
  const react = {
    useRef(initial) { const i = cursor++; return slots[i] ??= { current: initial }; },
    useState(initial) {
      const i = cursor++; if (!(i in slots)) slots[i] = initial;
      return [slots[i], value => { slots[i] = typeof value === 'function' ? value(slots[i]) : value; }];
    },
    useEffect(fn, deps) {
      const i = cursor++, prior = slots[i];
      if (!prior || deps.some((dep, index) => !Object.is(dep, prior.deps[index]))) pending.push(() => {
        prior?.cleanup?.(); slots[i] = { deps, cleanup: fn() };
      });
    },
  };
  const jsx = (type, properties) => ({ type, props: properties || {} });
  const source = readFileSync(new URL('../src/components/ui/MediaViewer.tsx', import.meta.url), 'utf8');
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  const exports = {};
  vm.runInNewContext(code, { exports, URL, document, HTMLElement: Element, require: id => {
    if (id === 'react') return react;
    if (id === 'react/jsx-runtime') return { jsx, jsxs: jsx, Fragment: 'fragment' };
    if (id === './mediaViewerUtils') return helpers;
    if (id === './Button') return { Button: 'Button' };
    if (id.includes('.css')) return {};
    return require(id);
  } });
  props = { items: initialItems, activeIndex: 0, onIndexChange: index => calls.indices.push(index), onClose: () => calls.closed++ };
  function renderHost(changes = {}) {
    Object.assign(props, changes); cursor = 0; pending = [];
    tree = exports.MediaViewer(props);
    tree.props.ref.current = dialog;
    for (const effect of pending) effect();
    return tree;
  }
  function find(predicate, node = tree) {
    if (!node || typeof node !== 'object') return undefined;
    if (predicate(node)) return node;
    const children = node.props?.children;
    for (const child of Array.isArray(children) ? children.flat(Infinity) : [children]) {
      if (!child || typeof child !== "object") continue;
      const result = find(predicate, child); if (result) return result;
    }
  }
  return { render: renderHost, find, calls, dialog, document };
}
const images = [
  { id: 'a', src: '/a.png', type: 'image', title: 'A' },
  { id: 'b', src: '/b.png', type: 'image', title: 'B' },
];
test('dialog lifecycle restores the original scroll and focus on close', () => {
  const host = viewerHost(images); host.render();
  assert.equal(host.calls.shown, 1); assert.equal(host.document.body.style.overflow, 'hidden');
  host.render({ activeIndex: null });
  assert.equal(host.calls.hidden, 1); assert.equal(host.document.body.style.overflow, 'auto'); assert.equal(host.calls.focused, 1);
});
test('arrow navigation ignores video controls and form inputs', () => {
  const host = viewerHost(images); const tree = host.render();
  let prevented = 0;
  const event = tagName => ({ key: 'ArrowRight', target: { tagName, getAttribute: () => null }, preventDefault() { prevented++; } });
  tree.props.onKeyDown(event('VIDEO')); tree.props.onKeyDown(event('INPUT'));
  assert.deepEqual(host.calls.indices, []); assert.equal(prevented, 0);
  tree.props.onKeyDown(event('BUTTON'));
  assert.deepEqual(host.calls.indices, [1]); assert.equal(prevented, 1);
});
test('media failure exposes retry and retry remounts the media', () => {
  const host = viewerHost(images); host.render();
  host.find(node => node.type === 'img').props.onError(); host.render();
  assert.ok(host.find(node => node.props?.role === 'alert'));
  host.find(node => node.type === 'Button' && node.props.children === 'Retry').props.onClick(); host.render();
  assert.ok(host.find(node => node.type === 'img'));
  assert.equal(host.find(node => node.props?.role === 'alert'), undefined);
});


test('provider snapshots collections and bounds navigation using current state', () => {
  let state;
  const context = { Provider: 'Provider' };
  const react = {
    createContext: () => context,
    useContext: value => value.current ?? null,
    useState: initial => [state ??= initial, update => { state = typeof update === 'function' ? update(state) : update; }],
    useCallback: callback => callback,
  };
  const jsx = (type, props) => ({ type, props });
  const source = readFileSync(new URL('../src/components/ui/MediaViewerProvider.tsx', import.meta.url), 'utf8');
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  const exports = {};
  vm.runInNewContext(code, { exports, require: id => {
    if (id === 'react') return react;
    if (id === 'react/jsx-runtime') return { jsx, jsxs: jsx };
    if (id === './MediaViewer') return { MediaViewer: 'MediaViewer' };
    if (id === './mediaViewerUtils') return helpers;
    return require(id);
  } });
  const render = () => exports.MediaViewerProvider({ children: 'content' });
  let tree = render(), api = tree.props.value;
  const collection = [...images];
  api.open(collection, 99); collection.pop();
  tree = render(); api = tree.props.value;
  assert.equal(api.items.length, 2); assert.equal(api.activeIndex, 1);
  api.previous(); api.previous(); api.next();
  tree = render(); api = tree.props.value;
  assert.equal(api.activeIndex, 1);
  assert.equal(tree.props.children[1].props.activeIndex, 1);
  api.goTo(-10); assert.equal(render().props.value.activeIndex, 0);
  api.close(); api = render().props.value;
  assert.equal(api.activeIndex, null); assert.equal(api.items.length, 0);
  api.next(); assert.equal(render().props.value.activeIndex, null);
  api.open([], 0); assert.equal(render().props.value.activeIndex, null);
  assert.throws(() => exports.useMediaViewer(), /within MediaViewerProvider/);
  context.current = render().props.value;
  assert.equal(exports.useMediaViewer(), context.current);
});
