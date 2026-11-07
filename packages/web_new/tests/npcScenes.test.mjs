import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

function compile(path, jsx = false) {
  return ts.transpileModule(readFileSync(new URL(path, import.meta.url), 'utf8'), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: jsx ? ts.JsxEmit.ReactJSX : undefined },
  }).outputText;
}
function nodes(node) {
  return !node || typeof node !== 'object' ? [] : [node, ...[node.props?.children].flat(Infinity).flatMap(nodes)];
}
const colors = {};
vm.runInNewContext(compile('../src/data/gameTextColors.ts'), { exports: colors });
const localized = {};
vm.runInNewContext(compile('../src/data/localized.ts'), { exports: localized });
const gameText = {};
vm.runInNewContext(compile('../src/data/gameText.ts'), { exports: gameText, require: id => id === './localized' ? localized : colors });
const helpers = {};
vm.runInNewContext(compile('../src/data/npcContext.ts'), { exports: helpers });
const code = compile('../src/components/profile/NpcFlowParticipants.tsx', true);
const tick = () => new Promise(resolve => setImmediate(resolve));

function harness(options = {}) {
  const requests = [];
  const scenes = Array.from({ length: 105 }, (_, i) => ({
    state_id: i + 1, flow_state: `TECHNICAL_FLOW_${i + 1}`, title: `Conversation <color=Highlight>${i + 1}</color>`,
    speakers: [{ label: '{message} Abby' }, { label: '{PlayerName}' }, { label: 'speaker:123' }],
    preview: [{ canonical_key: `line:${i}`, text: `Preview <color=Highlight>${i + 1}</color>`, speaker: null }], quests: [],
  }));
  const lines = Array.from({ length: 25 }, (_, i) => ({ canonical_key: `line:${i}`, text: `Line <color=Highlight>${i + 1}</color>`,
    speaker: { label: i === 0 ? '{message} Abby' : i === 1 ? '{PlayerName}' : 'speaker:123' } }));
  if (options.preview) scenes[0].preview = options.preview;
  if (options.lines) lines.splice(0, lines.length, ...options.lines);
  let active;
  const exports = {};
  vm.runInNewContext(code, { exports, URLSearchParams, AbortController, require: id => {
    if (id === 'react') return {
      useState: initial => { const ctx = active, i = ctx.index++; if (!(i in ctx.slots)) ctx.slots[i] = initial;
        return [ctx.slots[i], value => { ctx.slots[i] = typeof value === 'function' ? value(ctx.slots[i]) : value; }]; },
      useEffect: (run, deps) => { const ctx = active, i = ctx.effectIndex++;
        if (!ctx.effects[i] || deps.some((value, j) => value !== ctx.effects[i].deps[j]))
          ctx.pending.push(() => { ctx.effects[i]?.cleanup?.(); ctx.effects[i] = { deps, cleanup: run() }; }); },
    };
    if (id === 'react/jsx-runtime') return { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }), Fragment: 'Fragment' };
    if (id === '@/hooks/useLocale') return { useLocale: () => 'en' };
    if (id === '@/api/client') return { api: async url => {
      requests.push(url);
      const parsed = new URL(url, 'https://example.test'), offset = Number(parsed.searchParams.get('offset'));
      const source = parsed.pathname.endsWith('/scenes') ? scenes : lines;
      const limit = Number(parsed.searchParams.get('limit'));
      return { results: source.slice(offset, offset + limit), total: source.length };
    } };
    if (id === '@/data/npcContext') return helpers;
    if (id === '@/data/gameText') return gameText;
    if (id === '@/data/localized') return { localizedText: (value, fallback = '') => value?.content || value || fallback };
    if (id === '@/lib/router') return { Link: 'Link' };
    if (id === '@/lib/routeKey') return { questPath: quest => `/quests/${quest.canonical_key}` };
    if (id === '@/components/ui/Pagination') return { Pagination: 'Pagination' };
    return new Proxy({}, { get: (_, name) => name });
  } });
  function component(fn, props) {
    const ctx = { slots: [], effects: [] };
    return () => {
      ctx.index = ctx.effectIndex = 0; ctx.pending = []; active = ctx;
      const tree = fn(props);
      for (const effect of ctx.pending) effect();
      return nodes(tree);
    };
  }
  return { requests, render: component(exports.NpcFlowParticipants, { characterKey: 'character:npc:17288', gameVersion: '3.7', display: { name: 'Astra', colorMode: 'accent', customColor: '', gender: 'female' } }), component };
}

test('scene pages reach entries beyond 100 and never display technical flow keys', async () => {
  const h = harness();
  const loading = h.render().find(node => node.type === 'SkeletonGroup');
  assert.equal(loading.props.label, 'Loading conversations');
  assert.equal(loading.props.children.length, 4);
  await tick();
  let tree = h.render();
  const paging = tree.find(node => node.type === 'Pagination');
  assert.equal(paging.props.total, 105);
  assert.equal(paging.props.pageSize, 8);
  paging.props.onPageChange(14);
  h.render(); await tick(); tree = h.render();
  const params = new URL(h.requests.at(-1), 'https://example.test').searchParams;
  assert.equal(params.get('offset'), '104');
  assert.equal(params.get('limit'), '8');
  assert.equal(params.get('locale'), 'en');
  assert.equal(params.get('game_version'), '3.7');
  const scene = tree.find(node => typeof node.type === 'function' && node.type.name === 'Conversation');
  assert.equal(scene.props.scene.title, 'Conversation <color=Highlight>105</color>');
  const renderScene = h.component(scene.type, scene.props);
  const display = renderScene();
  assert(display.some(node => node.type === 'PlayerText' && node.props.value === 'Conversation <color=Highlight>105</color>'));
  assert(!JSON.stringify(display).includes('TECHNICAL_FLOW_'));
  assert(display.some(node => node.type === 'PlayerText' && node.props.value === 'Preview <color=Highlight>105</color>'));
  const speakers = display.find(node => node.props.className === 'npc-scene-speakers');
  assert.deepEqual(nodes(speakers).filter(node => node.type === 'PlayerText').map(node => node.props.value), ['Abby', '{PlayerName}', 'Unknown speaker']);
  assert(nodes(speakers).filter(node => node.type === 'PlayerText').every(node => node.props.display.name === 'Astra'));
});

test('inline conversation fetches state-scoped dialogue in pages of 20 with normalized speakers', async () => {
  const h = harness(); h.render(); await tick();
  const scene = h.render().find(node => typeof node.type === 'function' && node.type.name === 'Conversation');
  const renderScene = h.component(scene.type, scene.props);
  let tree = renderScene();
  assert.equal(h.requests.length, 1);
  tree.find(node => node.type === 'details').props.onToggle({ currentTarget: { open: true } });
  tree = renderScene();
  assert.equal(tree.find(node => node.type === 'SkeletonGroup').props.label, 'Loading conversation');
  await tick(); tree = renderScene();
  const first = new URL(h.requests.at(-1), 'https://example.test');
  assert.equal(first.pathname, '/npcs/character%3Anpc%3A17288/dialogue');
  assert.equal(first.searchParams.get('state_id'), '1');
  assert.equal(first.searchParams.get('limit'), '20');
  assert.equal(first.searchParams.get('offset'), '0');
  assert.equal(first.searchParams.get('game_version'), '3.7');
  assert.equal(first.searchParams.get('locale'), 'en');
  const names = tree.filter(node => node.type === 'strong').map(node => node.props.children.props.value);
  assert.deepEqual(names.slice(0, 3), ['Abby', '{PlayerName}', 'Unknown speaker']);
  const pagination = tree.find(node => node.type === 'Pagination');
  assert.equal(pagination.props.pageSize, 20);
  assert.equal(pagination.props.total, 25);
  pagination.props.onPageChange(2); renderScene(); await tick(); tree = renderScene();
  const second = new URL(h.requests.at(-1), 'https://example.test');
  assert.equal(second.searchParams.get('state_id'), '1');
  assert.equal(second.searchParams.get('offset'), '20');
  assert.equal(tree.filter(node => node.props.className === 'npc-scene-line').length, 5);
  assert(tree.some(node => node.type === 'PlayerText' && node.props.value === 'Line <color=Highlight>25</color>' && node.props.display.name === 'Astra'));
  assert(!JSON.stringify(tree).includes('TECHNICAL_FLOW_'));
});

test('unavailable preview and conversation text render a single explanation with pagination', async () => {
  const empty = i => ({ canonical_key: `line:${i}`, text: i % 2 ? null : { content: '   ' }, speaker: { label: 'Cristoforo' } });
  const h = harness({ preview: [empty(1), empty(2)], lines: Array.from({ length: 25 }, (_, i) => empty(i)) });
  h.render(); await tick();
  const scene = h.render().find(node => typeof node.type === 'function' && node.type.name === 'Conversation');
  const renderScene = h.component(scene.type, scene.props);
  let tree = renderScene();
  const preview = tree.find(node => node.props.className === 'npc-scene-preview');
  assert.equal(nodes(preview).filter(node => node.type === 'p' && node.props.children === 'Dialogue text is unavailable in this language.').length, 1);
  tree.find(node => node.type === 'details').props.onToggle({ currentTarget: { open: true } });
  renderScene(); await tick(); tree = renderScene();
  const lines = tree.find(node => node.props.className === 'npc-scene-lines');
  assert.equal(nodes(lines).filter(node => node.type === 'p' && node.props.children === 'Dialogue text is unavailable in this language.').length, 1);
  assert.equal(nodes(lines).filter(node => node.type === 'strong').length, 0);
  assert.equal(tree.find(node => node.type === 'Pagination').props.total, 25);
});

test('mixed conversation text keeps speakers and supplies a fallback only for missing lines', async () => {
  const lines = [{ canonical_key: 'line:1', text: '<i></i>', speaker: { label: 'Cristoforo' } },
    { canonical_key: 'line:2', text: { inline_text: 'Hello' }, speaker: { label: 'Visitor' } }];
  const h = harness({ preview: lines, lines });
  h.render(); await tick();
  const scene = h.render().find(node => typeof node.type === 'function' && node.type.name === 'Conversation');
  const renderScene = h.component(scene.type, scene.props);
  let tree = renderScene();
  tree.find(node => node.type === 'details').props.onToggle({ currentTarget: { open: true } });
  renderScene(); await tick(); tree = renderScene();
  const items = tree.filter(node => node.props.className === 'npc-scene-line');
  assert.equal(items.length, 2);
  const first = nodes(items[0]).find(node => node.type === 'PlayerText' && node.props.fallback);
  assert.equal(first.props.value, null);
  assert.equal(first.props.fallback, 'Dialogue text is unavailable in this language.');
  assert.equal(nodes(items[1]).find(node => node.type === 'PlayerText' && node.props.fallback).props.value.inline_text, 'Hello');
});
