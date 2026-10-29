import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const require = createRequire(import.meta.url);
const source = readFileSync(new URL('../src/components/story/QuestMediaReferences.tsx', import.meta.url), 'utf8');
const code = ts.transpileModule(source, { compilerOptions: {
  module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true,
} }).outputText;
const exports = {};
const mocks = {
  'lucide-react': new Proxy({}, { get: () => 'icon' }),
  '@/lib/router': { Link: 'atlas-link' },
  '../../preferences/NarrativePreferences': {},
  './cutsceneAnchor': { cutsceneAnchor: () => 'scene' },
};
vm.runInNewContext(code, { exports, require: (id) => mocks[id] ?? require(id) });

test('package-linked scenes remain navigable without a claimed transcript position', () => {
  const tree = exports.QuestMediaReferences({ manifest: {
    game_version: '3.3.0', events: [], video_packages: [],
    related_cutscenes: [{ name: 'C0028', playable: false, context_inferred: true }],
  }, stateAnchors: new Map() });
  const links = [];
  const text = [];
  function visit(node) {
    if (typeof node === 'string') text.push(node);
    if (!node || typeof node !== 'object') return;
    if (node.type === 'atlas-link') links.push(node.props.to);
    for (const child of [node.props?.children].flat(Infinity)) visit(child);
  }
  visit(tree);
  assert.deepEqual(links, ['/cutscenes/C0028?game_version=3.3.0']);
  assert.ok(text.some((value) => value.includes('exact story position is unknown')));
  assert.ok(text.some((value) => value.includes('Video not available yet')));
});
