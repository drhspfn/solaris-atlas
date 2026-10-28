import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const require = createRequire(import.meta.url);
function archive(queryText) {
  let query = new URLSearchParams(queryText);
  const dummy = new Proxy({}, { get: () => () => null });
  const source = readFileSync(new URL('../src/components/story/CutsceneArchive.tsx', import.meta.url), 'utf8');
  const code = ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true,
  } }).outputText;
  const exports = {};
  const mocks = {
    react: { useState: (value) => [value, () => {}], useEffect() {}, useRef: (value) => ({ current: value }) },
    'lucide-react': dummy, '@/api/client': dummy, '@/hooks/useLocale': { useLocale: () => 'en' },
    '@/lib/router': { Link: 'a', useSearchParams: () => [query, (update) => { query = update(query); }] },
    '@/lib/routeKey': dummy, '@/components/ui/Feedback': dummy,
    '@/components/ui/Select': { Select: 'atlas-select' }, '@/components/ui/InfiniteScroll': { InfiniteScroll: 'infinite-scroll' },
    './QuestCutscenes': dummy, './cutsceneAnchor': dummy,
  };
  vm.runInNewContext(code, { exports, require: (id) => mocks[id] ?? require(id), URLSearchParams });
  const tree = exports.CutsceneArchive({ initial: null, versions: ['2.10.0', '2.9.0'] });
  const selects = [];
  function visit(node) {
    if (!node || typeof node !== 'object') return;
    if (node.type === 'atlas-select') selects.push(node.props);
    for (const child of [node.props?.children].flat(Infinity)) visit(child);
  }
  visit(tree);
  return { selects, query: () => query };
}

test('shared version selector includes All and applies immediately while resetting pagination', () => {
  const view = archive('q=M03&offset=48&playable=false&game_version=2.8.0');
  const version = view.selects.find((item) => item.label === 'Game version');
  assert.equal(version.options[0].label, 'All versions');
  version.onChange('');
  assert.equal(view.query().get('game_version'), null);
  assert.equal(view.query().get('offset'), null);
  assert.equal(view.query().get('q'), 'M03');
  assert.equal(view.query().get('playable'), 'false');
});

test('availability selector also applies immediately and All versions is the default', () => {
  const view = archive('offset=24');
  assert.equal(view.selects.find((item) => item.label === 'Game version').value, '');
  view.selects.find((item) => item.label === 'Availability').onChange('false');
  assert.equal(view.query().get('playable'), 'false');
  assert.equal(view.query().get('offset'), null);
});


test('cutscene playback uses the shared language selector and disables missing tracks', () => {
  const source = readFileSync(new URL('../src/components/story/CutsceneControls.tsx', import.meta.url), 'utf8');
  const code = ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true,
  } }).outputText;
  const exports = {};
  let stateIndex = 0;
  const dummy = new Proxy({}, { get: () => () => null });
  const mocks = {
    react: { useState: (value) => [stateIndex++ === 0 ? true : value, () => {}] },
    'lucide-react': dummy, '../ui/Select': { Select: 'atlas-select' },
    '../../config/settings': { APP_SETTINGS: { cutscene: { masterVolume: .7 } } },
  };
  vm.runInNewContext(code, { exports, require: (id) => mocks[id] ?? require(id) });
  const changes = [];
  const tree = exports.CutsceneControls({
    timeline: { clips: [], decisions: [], total: 10, complete: true }, position: 0,
    volume: .7, languages: ['en', 'ja'], language: 'en', onLanguage: (value) => changes.push(value),
  });
  let selector;
  function visit(node) {
    if (!node || typeof node !== 'object') return;
    if (node.type === 'atlas-select') selector = node.props;
    for (const child of [node.props?.children].flat(Infinity)) visit(child);
  }
  visit(tree);
  assert.equal(selector.label, 'Cutscene voice language');
  assert.equal(selector.options.find((item) => item.value === 'ko').disabled, true);
  assert.equal(selector.options.find((item) => item.value === 'ja').disabled, false);
  selector.onChange('ja');
  assert.deepEqual(changes, ['ja']);
});
