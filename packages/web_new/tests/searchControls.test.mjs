import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
const require = createRequire(import.meta.url);
function harness(name, query, props = {}) {
  let cursor = 0; const slots = [], navigations = [], exports = {};
  const mocks = {
    react: { useState(value) { const i = cursor++; if (!(i in slots)) slots[i] = value; return [slots[i], next => { slots[i] = typeof next === 'function' ? next(slots[i]) : next; }]; }, useRef: () => ({ current: null }), useEffect() {} },
    '@/lib/router': { Link: 'a', useSearchParams: () => new URLSearchParams(query), useNavigate: () => value => navigations.push(value) },
    '@/hooks/useLocale': { useLocale: () => 'en' },
    '@/components/ui/Button': { Button: 'button' }, '@/components/ui/Input': { Input: 'input' }, '@/components/ui/Pill': { Pill: 'Pill' },
    '@/data/entities': { categories: [{ key: 'character', label: 'Characters' }, { key: 'quest', label: 'Quests' }], categoryTitle: { character: 'Characters', quest: 'Quests' } },
    '@/config/settings': { APP_SETTINGS: { presentation: { skeletonCount: 1 } } },
  };
  const code = ts.transpileModule(readFileSync(new URL(`../src/components/search/${name}.tsx`, import.meta.url), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  vm.runInNewContext(code, { exports, URLSearchParams, require: id => mocks[id] ?? (id.endsWith('.css') ? {} : id.startsWith('@/') ? {} : require(id)) });
  return { render() { cursor = 0; return exports[name](props); }, navigations };
}
function nodes(node) { return !node || typeof node !== 'object' ? [] : [node, ...[node.props?.children].flat(Infinity).flatMap(nodes)]; }
test('shared search controls retain query scope and clear through an icon-only action', () => {
  const h = harness('SearchBox', 'q=old&category=character&locale=ru&mode=story&game_version=3.7', { initial: 'old' });
  let tree = h.render();
  nodes(tree).find(node => node.type === 'input').props.onChange({ target: { value: 'new question' } });
  tree = h.render(); tree.props.onSubmit({ preventDefault() {} });
  const next = new URL(h.navigations[0], 'https://atlas.test').searchParams;
  for (const [key, value] of Object.entries({ q: 'new question', category: 'character', locale: 'ru', mode: 'story', game_version: '3.7' })) assert.equal(next.get(key), value);
  const submit = nodes(tree).find(node => node.type === 'button' && node.props.type === 'submit');
  assert.equal(submit.props.children, undefined);
  assert.equal(submit.props.className, 'search-action');
  assert.equal(submit.props.size, 'icon');
  assert.equal(submit.props.variant, 'ghost');
  const clear = nodes(tree).find(node => node.props['aria-label'] === 'Clear archive search');
  assert.equal(clear.props.children, undefined); assert(clear.props.icon); assert.equal(clear.props.variant, 'ghost');
  clear.props.onClick(); assert.equal(new URL(h.navigations[1], 'https://atlas.test').searchParams.has('q'), false);
  assert.equal(nodes(h.render()).find(node => node.type === 'input').props.value, '');
});
test('search category pills preserve locale and expose the selected destination', () => {
  const tree = harness('SearchClient', 'q=Denia&locale=ru&category=character').render();
  const pills = nodes(tree).filter(node => node.type === 'Pill');
  const character = pills.find(node => node.props.children === 'Characters');
  assert.equal(character.props.selected, true);
  const quests = pills.find(node => node.props.children === 'Quests');
  const params = new URL(quests.props.href, 'https://atlas.test').searchParams;
  assert.equal(params.get('q'), 'Denia'); assert.equal(params.get('locale'), 'ru'); assert.equal(params.get('category'), 'quest');
});
