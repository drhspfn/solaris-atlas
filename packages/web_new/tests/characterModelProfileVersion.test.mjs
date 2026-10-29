import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

test('character page sends its selected version to the model and import actions', () => {
  const components = new Proxy({}, { get: (_, name) => name });
  const mocks = {
    react: { useState: (value) => [value, () => {}], useEffect: () => {} },
    'react/jsx-runtime': {
      jsx: (type, props) => ({ type, props }),
      jsxs: (type, props) => ({ type, props }),
      Fragment: 'Fragment',
    },
    '@/lib/router': { Link: 'Link', useSearchParams: () => new URLSearchParams('game_version=3.5.0') },
    '@/hooks/useLocale': { useLocale: () => 'en' },
    '@/hooks/usePlayerDisplay': { usePlayerDisplay: () => ({}) },
    '@/data/localized': { localizedText: (value, fallback = '') => value?.content || fallback },
    '@/data/entities': { categoryTitle: { character: 'Characters' } },
    '@/config/settings': { APP_SETTINGS: { limits: { relatedPreview: 5, biographyPreviewCharacters: 100 } } },
  };
  const exports = {};
  const source = readFileSync(new URL('../src/components/profile/EntityProfileClient.tsx', import.meta.url), 'utf8');
  const code = ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX,
  } }).outputText;
  vm.runInNewContext(code, { exports, require: (id) => mocks[id] ?? components, URLSearchParams });
  const root = exports.EntityProfileClient({ kind: 'character', entityKey: 'denia', initialData: {
    character: { canonical_key: 'character:1211', name: { content: 'Denia' } },
  } });
  const collect = (node) => !node || typeof node !== 'object' ? [] : [node, ...[node.props?.children].flat(Infinity).flatMap(collect)];
  const nodes = collect(root);
  assert.equal(nodes.find((node) => node.type === 'CharacterModel').props.gameVersion, '3.5.0');
  assert.equal(nodes.find((node) => node.type === 'CharacterModelImport').props.initialGameVersion, '3.5.0');
});
