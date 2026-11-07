import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/components/story/QuestContinuity.tsx', import.meta.url), 'utf8');
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText;
function nodes(node) { return !node || typeof node !== 'object' ? [] : [node, ...[node.props?.children].flat(Infinity).flatMap(nodes)]; }
test('quest continuity passes display to chapter, type, current quest and neighbors', () => {
  const components = new Proxy({}, { get: (_, name) => name });
  const mocks = {
    '@/lib/routeKey': { questPath: (quest) => `/quests/${quest.game_quest_id}` },
    'react/jsx-runtime': { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }) },
  };
  const display = { name: 'Rover', gender: 'female' };
  const neighbor = { relation: 'quest_tree_next', quest_ids: [42], name: { content: 'Next {PlayerName}' }, source: { file: 'source', raw_path: '$[0]' } };
  const continuity = { quest: 'quest:1', selected_game_version: '1.0', quest_type: { name: { content: 'Type {PlayerName}' } },
    quest_chapter: {}, quest_tree_nodes: [{ name: { content: 'Current {PlayerName}' }, tree_chapter_name: { content: 'Chapter {PlayerName}' }, canonical_key: 'tree:1', neighbors: [neighbor] }] };
  const exports = {};
  vm.runInNewContext(code, { exports, require: (id) => mocks[id] ?? components });
  const tree = nodes(exports.QuestContinuity({ continuity, title: 'Quest', display }));
  const current = tree.filter((node) => node.type === 'PlayerText');
  assert(current.some((node) => node.props.value === 'Chapter {PlayerName}' && node.props.display === display));
  assert(current.some((node) => node.props.value?.content === 'Current {PlayerName}' && node.props.display === display));
  const next = tree.find((node) => typeof node.type === 'function' && node.props.direction === 'next');
  assert.equal(next.props.display, display);
  const link = nodes(next.type(next.props)).find((node) => node.type === 'PlayerText');
  assert.equal(link.props.value, 'Next {PlayerName}');
  assert.equal(link.props.display, display);
});
