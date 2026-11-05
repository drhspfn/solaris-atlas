import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';
import test from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import ts from 'typescript';
import { encounterFlows, encounterFlowOwners, encounterCinematicParts, encounterOwnsMedia, encounterAnchor } from '../src/components/story/encounterTransitions.ts';
import { mediaViewerUrl } from '../src/components/ui/mediaViewerUtils.ts';

const require = createRequire(import.meta.url);
const source = { file: 'private-source.json', key: '114750015', raw_path: '$.Data' };
const cinematic = (overrides = {}) => ({ kind: 'sequence', reference: 'asset:ue:/Game/M0334/DA_M0334.DA_M0334',
  source, raw_path: '$.Actions[0].Params.SequenceDataAsset', basis: 'source', sequence_names: ['M0334_1Z'],
  renderable: false, playback_status: 'unresolved', async: false, boundary_semantics: 'authored_action_extent',
  start: { action_index: 0, anchor: 'before_action' }, end: { action_index: 0, anchor: 'after_action' }, ...overrides });
const flow = (id, state = 'Flow29', overrides = {}) => ({ id, state, state_node_id: 99, phase: 'EnterActions',
  execution_context: 'authored', async: true, timing: 'during', cinematics: [], source, ...overrides });
const node = (id, overrides = {}) => ({ id, owner: `level_play_node:${id}`, node_type: 'ChildQuest', path: [],
  children_order: 'unknown', title: 'Complete the encounter', condition: { Type: 'WaitBattleCondition', EntityId: 114750015 },
  children: [], flow_states: [], media: [], completions: [], source, ...overrides });
const encounter = (root, overrides = {}) => ({ id: 'gate/dungeon:1555', kind: 'encounter', dungeon_id: 1555,
  title: 'Battle', release_id: 21, status: 'verified', trees_order: 'unknown', source,
  trees: [{ level_play_id: 114000004, ordering: 'authored_tree', membership: source, root }], ...overrides });
const display = { name: 'Rover', colorMode: 'accent', customColor: '' };
const opened = [];
const cache = new Map();
function loadStory(name) {
  if (cache.has(name)) return cache.get(name);
  let path = new URL(`../src/components/story/${name}.tsx`, import.meta.url);
  if (!existsSync(path)) path = new URL(`../src/components/story/${name}.ts`, import.meta.url);
  const code = ts.transpileModule(readFileSync(path, 'utf8'), { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true,
  } }).outputText;
  const exports = {}; cache.set(name, exports);
  vm.runInNewContext(code, { exports, URL, require: id => {
    if (id.endsWith('.module.css')) return { __esModule: true, default: new Proxy({}, { get: (_, key) => String(key) }) };
    if (id === 'lucide-react') return new Proxy({}, { get: () => () => null });
    if (id === '../dialogue/PlayerText') return { PlayerText: ({ value, fallback }) => typeof value === 'string' ? value : value?.content || fallback };
    if (id === '../ui/Button') return { Button: ({ children, onClick }) => React.createElement('button', { onClick }, children) };
    if (id === '../ui/MediaViewerProvider') return { useMediaViewer: () => ({ open: (...args) => opened.push(args) }) };
    if (id === '../ui/mediaViewerUtils') return { mediaViewerUrl };
    if (id.startsWith('./')) return loadStory(id.slice(2));
    return require(id);
  } });
  return exports;
}
const { QuestEncounter, EncounterCinematicBoundary } = loadStory('QuestEncounter');
const { QuestSequenceFragments, playableEncounterFragments } = loadStory('QuestSequenceFragments');
const { runtimeBlockSlots } = loadStory('QuestRuntimeBlock');

test('unplaced activities retain their identities outside chronological slots', () => {
  const lines = [{ story_position: [2] }, { story_position: [4] }];
  const known = { id: 'known', position: [3] };
  const unknown = { id: 'unknown', position: null };
  const related = { id: 'related', position: [0], ordering: 'related' };
  for (const partial of [false, true]) {
    const slots = runtimeBlockSlots(lines, [unknown, known, related], partial);
    assert.deepEqual(Array.from(slots.get(-1), (block) => block.id), ['unknown', 'related']);
    assert.deepEqual(Array.from(slots.get(1), (block) => block.id), ['known']);
    assert.equal(slots.has(0), false);
    assert.equal(slots.has(2), false);
  }
});
const render = (data, props = {}) => renderToStaticMarkup(React.createElement(QuestEncounter,
  { encounter: data, display, stateAnchors: new Map(), ...props }));

function allNodes(tree) {
  if (!tree || typeof tree !== 'object') return [];
  return [tree, ...[tree.props?.children].flat(Infinity).flatMap(allNodes)];
}

test('local sequence keeps authored child order, parallel occurrences remain distinct', () => {
  const data = encounter(node('root', { condition: null, children_order: 'sequence', children: [
    node('67', { flow_states: [flow('first', 'Flow27')] }),
    node('48', { condition: null, children_order: 'parallel', children: [
      node('56', { flow_states: [flow('parallel-a')] }), node('58', { flow_states: [flow('parallel-b')] }),
    ] }), node('68', { flow_states: [flow('last', 'Flow30')] }),
  ] }));
  assert.deepEqual(encounterFlows(data).map(f => f.id), ['first', 'parallel-a', 'parallel-b', 'last']);
  const markup = render(data);
  assert.match(markup, /aria-label="Authored action order"/);
  assert.match(markup, /Parallel paths · no order is implied/);
  assert.equal((markup.match(/Dialogue during the encounter/g) || []).length, 4);
  assert(markup.indexOf(encounterAnchor('67')) < markup.indexOf(encounterAnchor('48')));
});

test('different LevelPlay contexts and unresolved membership never become one timeline', () => {
  const data = encounter(node('first'), { status: 'partial', trees: [
    { level_play_id: 114000004, ordering: 'authored_tree', membership: source, root: node('first') },
    { level_play_id: 114750000, ordering: 'authored_tree', membership: source, root: node('second') },
  ], unresolved_level_plays: [{ level_play_id: 100, membership: source, reason: 'invalid_authored_tree', diagnostic: 'missing_root' }] });
  const markup = render(data), visible = markup.split('<details class="source">')[0];
  assert.match(visible, /relative order of these encounter contexts is not established/);
  assert.match(visible, /matching authored tree is unavailable/);
  assert.doesNotMatch(visible, /<ol|Next phase|Then|114750000|114000004|EntityId|missing_root/);
});

test('SaveConfig stays separate and cannot own or hide flat authored dialogue', () => {
  const saved = flow('saved', 'SaveOnly', { phase: 'SaveConfig.Actions', execution_context: 'save_config', timing: 'unknown' });
  const data = encounter(node('root', { condition: null, flow_states: [saved], action_sections: [{
    phase: 'SaveConfig.Actions', execution_context: 'save_config', runtime_order: 'unknown', status: 'unsupported_shape',
    unprojected_actions: [{ index: 0, name: 'BuffTechnicalName' }], source,
  }] }));
  assert.equal(encounterFlowOwners([{ encounter: data }], [{ flow_state: 'SaveOnly' }]).size, 0);
  const markup = render(data);
  assert.match(markup, /Additional lifecycle context · order unknown/);
  assert.match(markup, /Dialogue · timing unknown/);
  assert.doesNotMatch(markup, /BuffTechnicalName|Before this action|After this action/);
});

test('canonical dialogue ownership is unique without deleting repeated branch calls', () => {
  const data = encounter(node('root', { children_order: 'parallel', children: [
    node('a', { flow_states: [flow('occurrence-a')] }), node('b', { flow_states: [flow('occurrence-b')] }),
  ] }));
  const owners = encounterFlowOwners([{ encounter: data }], [{ flow_state: 'Flow29' }, { flow_state: 'Mapped', story_position: [1] }]);
  assert.equal(owners.get('Flow29'), 'occurrence-a'); assert.equal(encounterFlows(data).length, 2);
  const markup = render(data, { stateAnchors: new Map([['Flow29', 'original-line']]), renderFlow: f =>
    f.id === owners.get(f.state) ? [React.createElement('p', { id: 'original-line', key: 'line' }, 'Authored dialogue')]
      : [React.createElement('a', { href: '#original-line', key: 'reference' }, 'Read shared dialogue')] });
  assert.equal((markup.match(/id="original-line"/g) || []).length, 1);
  assert.match(markup, /href="#original-line"/);
  assert.match(markup, new RegExp(encounterAnchor('occurrence-b')));
});

test('cinematic grouping uses exact action index, not flow name or translated text', () => {
  const lines = [{ id: 'before', action: { index: 0 } }, { id: 'SHUTUP', action: { index: 1 } },
    { id: 'THATNOTHOME', action: { index: 1 } }, { id: 'after', action: { index: 2 } }];
  const scene = cinematic({ start: { action_index: 1, anchor: 'before_action' }, end: { action_index: 1, anchor: 'after_action' } });
  const parts = encounterCinematicParts(lines, [scene]);
  assert.deepEqual(parts.map(p => p.lines.map(l => l.id)), [['before'], ['SHUTUP', 'THATNOTHOME'], ['after']]);
  assert.equal(parts[1].cinematics[0], scene);
  assert.equal(parts[0].cinematics.length, 0); assert.equal(parts[2].cinematics.length, 0);
  const unmatched = encounterCinematicParts([{ id: 'unknown' }], [scene]);
  assert.equal(unmatched[0].cinematics.length, 0); assert.equal(unmatched[1].lines.length, 0);
});

test('authored boundaries never claim a whole sequence is an MP4 or completed playback', () => {
  const markup = renderToStaticMarkup(React.createElement(EncounterCinematicBoundary, { media: [cinematic({ async: true })] },
    React.createElement('p', { id: 'line-SHUTUP' }, 'SHUT UP!')));
  assert.match(markup, /Start of cinematic action/); assert.match(markup, /End of cinematic action/);
  assert.match(markup, /do not establish playback duration or completion/);
  assert.doesNotMatch(markup, /<video|MP4|M0334_1Z|DA_M0334|Playback completed/);
});

test('only an exact cinematic reference can take an existing media event out of the flat reader', () => {
  const data = encounter(node('root', { flow_states: [flow('owner', 'Flow28', { cinematics: [cinematic()] })] }));
  const owners = encounterFlowOwners([{ encounter: data }], [{ flow_state: 'Flow28' }]);
  const exact = { reference: cinematic().reference, flow_state: 'Flow28' };
  assert.equal(encounterOwnsMedia(exact, owners, [{ encounter: data }]), true);
  assert.equal(encounterOwnsMedia({ ...exact, reference: 'cutscene:M0334' }, owners, [{ encounter: data }]), false);
  assert.equal(encounterFlowOwners([], [{ flow_state: 'Flow28' }]).size, 0);
});

test('published fragments deduplicate display files but retain original metadata and never crop', () => {
  const media = { schema_version: 1, complete_video: false, sections: [
    { kind: 'movie_fragment', status: 'published', file_id: 7, url: 'https://cdn.test/M0334.webm', range: { start: 12, duration: 2 }, clock: 'source' },
    { kind: 'movie_fragment', status: 'published', file_id: 7, url: 'https://cdn.test/M0334.webm', range: { start: 99 } },
    { kind: 'subsequence', reference: '/Game/NotAVideo' },
    { kind: 'movie_fragment', status: 'pending', file_id: 8, url: 'https://cdn.test/pending.webm' },
    { kind: 'movie_fragment', status: 'published', file_id: 9, url: 'javascript:alert(1)' },
    { kind: 'movie_fragment', status: 'published', file_id: 10, url: 'https://cdn.test/M0335.webm' },
  ] };
  const before = JSON.stringify(media), playable = playableEncounterFragments(media);
  assert.deepEqual(playable.map(p => p.file_id), [7, 10]); assert.equal(JSON.stringify(media), before);
  const tree = QuestSequenceFragments({ media });
  assert.equal(tree.props.items.length, 2);
  assert.equal(tree.props.items[0].src, 'https://cdn.test/M0334.webm');
  assert.equal(tree.props.items[0].start, undefined); assert.equal(tree.props.items[0].end, undefined);
  const list = tree.type(tree.props);
  allNodes(list).find(n => n.props?.onClick).props.onClick();
  assert.equal(opened.at(-1)[0][0].src, 'https://cdn.test/M0334.webm');
  assert.equal(renderToStaticMarkup(React.createElement(QuestSequenceFragments, { media: { ...media, sections: [] } })), '');
});


test('published video fragments are actually reachable inside their flow disclosure', () => {
  const data = encounter(node('root', { flow_states: [flow('movie-flow', 'Flow28', { sequence_media: {
    schema_version: 1, complete_video: false, sections: [{ kind: 'movie_fragment', status: 'published', file_id: 33,
      url: 'https://cdn.test/M0334.webm' }],
  } })] }));
  const markup = render(data);
  assert.match(markup, /Video fragments · 1/);
  assert.match(markup, /Watch video fragment 1/);
  assert.match(markup, /not a complete cinematic recording/);
  assert.doesNotMatch(markup, /<video/);
});


test('quest reader nests cinematic dialogue once and preserves cited line addresses', () => {
  const root = node('root', { condition: null, children_order: 'sequence', children: [
    node('cinema', { flow_states: [flow('cinema-occurrence', 'Flow28', { phase: 'Condition', async: false, timing: 'blocking', cinematics: [cinematic()] })] }),
    node('parallel', { condition: null, children_order: 'parallel', children: [
      node('a', { flow_states: [flow('repeat-a')] }), node('b', { flow_states: [flow('repeat-b')] }),
    ] }),
  ] });
  const data = encounter(root);
  const dummy = new Proxy({}, { get: () => () => null });
  const story = loadStory('QuestRuntimeBlock');
  const mocks = {
    react: React, 'lucide-react': dummy,
    '@/components/story/QuestRuntimeBlock': story,
    '@/components/story/QuestMediaReferences': { DialogueAudioReference: ({ children }) => children, QuestMediaReferences: () => null },
    '@/components/story/questReading': loadStory('questReading'),
    '@/components/story/cutscenePlacement': loadStory('cutscenePlacement'),
    '@/components/dialogue/PlayerText': { PlayerText: ({ value, fallback }) => typeof value === 'string' ? value : value?.content || fallback },
    '@/lib/router': { Link: ({ to, children }) => React.createElement('a', { href: to }, children),
      useSearchParams: () => new URLSearchParams('line=dialogue%3ASHUTUP'), useLocation: () => ({ key: 'fixture' }) },
    '@/hooks/useLocale': { useLocale: () => 'en' }, '@/hooks/usePlayerDisplay': { usePlayerDisplay: () => display },
    '@/preferences/NarrativePreferences': { useNarrativePreferences: () => ({ voiceLanguage: 'en' }) },
    '@/config/settings': { APP_SETTINGS: { limits: { transcript: 2000 } } },
  };
  const code = ts.transpileModule(readFileSync(new URL('../src/components/quest/QuestPageClient.tsx', import.meta.url), 'utf8'), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const exports = {};
  vm.runInNewContext(code, { exports, URLSearchParams,
    require: id => mocks[id] ?? (id === 'react/jsx-runtime' ? require(id) : dummy) });
  const lines = [
    { id: 'before', flow_state: 'Before', story_position: [0], text: { content: 'Before the encounter' } },
    { id: 'dialogue:SHUTUP', flow_state: 'Flow28', action: { index: 0 }, text: { content: 'SHUT UP!' }, story_order_mapped: false },
    { id: 'dialogue:THATNOTHOME', flow_state: 'Flow28', action: { index: 0 }, text: { content: 'THAT IS NOT HOME!' }, story_order_mapped: false },
    { id: 'dialogue:repeat', flow_state: 'Flow29', action: { index: 0 }, text: { content: 'A parallel call' }, story_order_mapped: false },
    { id: 'after', flow_state: 'After', story_position: [10], text: { content: 'After the encounter' } },
  ];
  const markup = renderToStaticMarkup(React.createElement(exports.QuestPageClient, {
    questKey: '114000031', initialSelection: { locale: 'en', gameVersion: '', citedLine: 'dialogue:SHUTUP' },
    initialProfile: { quest: { game_quest_id: 114000031, name: { content: 'Battle fixture' } } },
    initialTranscript: { lines, limit: 100, offset: 0, story_blocks: [
      { id: 'gate', title: 'Battle', position: [2], condition_type: 'FinishDungeon', branches: [], flow_states: [], source, encounter: data },
    ] }, initialMedia: { events: [], related_cutscenes: [] },
  }));
  for (const id of ['dialogue:SHUTUP', 'dialogue:THATNOTHOME', 'dialogue:repeat']) {
    assert.equal(markup.split(`data-dialogue-key="${id}"`).length - 1, 1);
  }
  const start = markup.indexOf('Start of cinematic action'), end = markup.indexOf('End of cinematic action');
  assert(start >= 0 && start < markup.indexOf('SHUT UP!') && markup.indexOf('THAT IS NOT HOME!') < end, JSON.stringify({start,end,shutup:markup.indexOf('SHUT UP!'),notHome:markup.indexOf('THAT IS NOT HOME!'),excerpt:markup.slice(Math.max(0,start),Math.max(0,start)+1800)}));
  assert.match(markup, /Read the dialogue shared by this branch/);
  assert.match(markup, /cited-passage/);
  assert.doesNotMatch(markup, /class="line-state">Flow28|class="line-state">Flow29/);
  const ids = [...markup.matchAll(/\bid="([^"]+)"/g)].map(match => match[1]);
  assert.equal(new Set(ids).size, ids.length, 'deep link targets must remain unique');
});
