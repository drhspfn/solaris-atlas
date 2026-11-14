import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import test from 'node:test';
import vm from 'node:vm';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import ts from 'typescript';

const require = createRequire(import.meta.url);
function compile(path, imports = {}) {
  const source = readFileSync(new URL(path, import.meta.url), 'utf8');
  const code = ts.transpileModule(source, { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX,
  } }).outputText;
  const exports = {};
  vm.runInNewContext(code, { exports, require: id => imports[id] ?? require(id) });
  return exports;
}

const primitive = ({ children }) => React.createElement('div', null, children);
const ui = new Proxy({}, { get: () => primitive });
const icons = new Proxy({}, { get: () => () => null });
const imports = {
  './types': compile('../src/components/models/types.ts'),
  '@/api/client': { apiUrl: url => url },
  '@/styles/model-viewer.css': {},
  'lucide-react': icons,
};
for (const name of ['Button', 'Tooltip', 'Checkbox', 'Input', 'Slider', 'Tabs', 'Select']) imports[`@/components/ui/${name}`] = ui;
imports['../ui/Skeleton'] = ui;
const { ModelViewer } = compile('../src/components/models/ModelViewer.tsx', imports);

test('download labels use clip counts and never expose pack IDs', () => {
  const asset = (url, size_bytes) => ({ url, size_bytes });
  const markup = renderToStaticMarkup(React.createElement(ModelViewer, { name: 'Character', active: false,
    assets: { preview: asset('/preview.glb', 1), download: null, physics: null,
      animations: asset('/legacy.glb', 10),
      animation_packs: [
        { ...asset('/shared.glb', 1048576), id: 'deadbeef', clips: ['a', 'b'] },
        { ...asset('/own.glb', 2097152), id: 'cafe1234', clips: ['c'] },
      ], animation_manifest: asset('/manifest.json', 1048576) } }));
  assert.match(markup, /Animations · 2 clips · 1\.0 MiB/);
  assert.match(markup, /Additional animations · 1 clip · 2\.0 MiB/);
  assert.match(markup, /Animation manifest · 1\.0 MiB/);
  assert.match(markup, /model-download-list/);
  assert.doesNotMatch(markup, /deadbeef|cafe1234|legacy\.glb/);
});

test('legacy download has a readable label without an invented clip count', () => {
  const markup = renderToStaticMarkup(React.createElement(ModelViewer, { name: 'Character', active: false,
    assets: { preview: { url: '/preview.glb', size_bytes: 1 }, download: null, physics: null,
      animations: { url: '/legacy.glb', size_bytes: 1048576 } } }));
  assert.match(markup, /Animations · 1\.0 MiB/);
  assert.doesNotMatch(markup, /Animations · \d+ clips/);
});
