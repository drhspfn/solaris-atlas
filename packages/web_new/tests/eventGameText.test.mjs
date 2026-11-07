import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/components/events/EventDetails.tsx', import.meta.url), 'utf8');
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText;
function nodes(node) { return !node || typeof node !== 'object' ? [] : [node, ...[node.props?.children].flat(Infinity).flatMap(nodes)]; }
test('event details pass source names and descriptions with player display to PlayerText', () => {
  const display = { name: 'Rover', colorMode: 'game', customColor: '' };
  const event = { id: 1, title: 'Hello {PlayerName}', description: 'Welcome {PlayerName}', kind: 'limited', starts_at: null,
    game_version: null, featured_names: ['{PlayerName}'], rewards: [{ id: 1, name: 'Reward {PlayerName}' }] };
  const components = new Proxy({}, { get: (_, name) => name });
  const mocks = {
    react: { useState: (value) => [value, () => {}], useEffect: () => {}, useRef: () => ({ current: null }) },
    './calendar': { kindLabels: { limited: 'Limited' }, artworkGroups: () => ({ artwork: [], resources: [] }) },
    'react/jsx-runtime': { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }) },
  };
  const exports = {};
  vm.runInNewContext(code, { exports, Intl, Date, require: (id) => mocks[id] ?? components });
  const tree = nodes(exports.EventDetails({ event, history: [], timeZone: 'UTC', now: null, onSelect() {}, onClose() {}, returnFocus: null, fallbackFocus: null, display }));
  const values = tree.filter((node) => node.type === 'PlayerText');
  for (const value of [event.title, event.description, event.featured_names[0], event.rewards[0].name]) {
    assert(values.some((node) => node.props.value === value && node.props.display === display));
  }
});
