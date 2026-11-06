import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';
import test from 'node:test';
import { localizedText } from '../src/data/localized.ts';
import { activityDestination, dialogueSearchIndex } from '../src/components/quest/questSidebarModel.ts';
import { isSourceOnlyActivity, sidebarActivityTitle } from '../src/components/story/runtimeActivity.ts';
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
const { QuestRuntimeBlock, runtimeBlockAnchor, runtimeBlockSlots } = loadStory('QuestRuntimeBlock');

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
const renderBlock = (block, props = {}) => renderToStaticMarkup(React.createElement(QuestRuntimeBlock,
  { block: { position: null, title: 'Story activity', branches: [], flow_states: [], source, ...block },
    display, stateAnchors: new Map(), ...props }));

test('source-only activities retain deep-link source evidence without an activity label', () => {
  const markup = renderBlock({ id: 'structural', condition_type: 'CheckConditionGroup',
    branches: [{ id: 'branch-a', type: 'Selector' }] });
  assert.match(markup, new RegExp(`^<details class="sourceOnly" id="${runtimeBlockAnchor('structural')}">`));
  assert.match(markup, /Source &amp; conditions/);
  assert.match(markup, /CheckConditionGroup/);
  assert.match(markup, /Selector · branch-a/);
  assert.doesNotMatch(markup, /\bopen=|<h3|Exploration|Activity context/);
});

test('compact activities preserve dialogue, optional interactions and unavailable transcript context', () => {
  const base = { id: 'activity', flow_states: [{ state: 'Known' }, { state: 'Outside' }],
    interactions: [{ id: 'inspect', title: 'Inspect the letter', flow_states: [{ state: 'Letter' }], source }] };
  const markup = renderBlock(base, { stateAnchors: new Map([['Known', 'line-known']]),
    renderInteraction: (states, owner) => {
      assert.deepEqual(states, ['Letter']); assert.equal(owner, 'activity:inspect');
      return [React.createElement('p', { id: 'line-letter', key: 'letter' }, 'All authored dialogue')];
    } });
  assert.match(markup, /href="#line-known"/);
  assert.match(markup, /id="line-letter"/);
  assert.match(markup, /Inspect the letter/);
  assert.doesNotMatch(markup, /<h3>Story activity/);
  assert.match(renderBlock(base), /Dialogue is outside this transcript window/);
});

test('branch explanations stay inside source disclosure while activity dialogue remains visible', () => {
  const markup = renderBlock({ id: 'branch', title: 'Find the letter',
    branches: [{ id: 'selector', type: 'Selector' }], flow_states: [{ state: 'Known' }] },
    { stateAnchors: new Map([['Known', 'line-known']]) });
  const visible = markup.split('<details>')[0];
  assert.match(visible, /Find the letter/);
  assert.match(visible, /href="#line-known"/);
  assert.doesNotMatch(visible, /Conditional|parallel|order.*established/);
  assert.match(markup, /Conditional or parallel branch; order is not established/);
});

test('actual 1557 instance exit is a requirement, never a second 1555 battle', () => {
  const fixture = JSON.parse(readFileSync(new URL('../../server/tests/fixtures/dungeon1557_exit.json', import.meta.url), 'utf8'));
  const condition = fixture.quest_node.Data.Condition;
  assert.equal(condition.Condition.IsExitDungeon, true);
  const markup = renderBlock({ id: fixture.quest_node.Key, title: 'Leave the area', condition_type: condition.Type,
    transition: { kind: 'dungeon_exit', dungeon_id: condition.Condition.DungeonId,
      map_config_id: fixture.instance.MapConfigId, is_exit_dungeon: true,
      boundary_semantics: 'authored_exit_requirement' } });
  const visible = markup.split('<details>')[0];
  assert.doesNotMatch(visible, /Area exit|Authored exit|another battle|completion is not established/);
  assert.match(markup, /Leave the area/);
  assert.match(markup, /Authored exit requirement; completion is not established/);
  assert.doesNotMatch(markup, /Encounter activities|End of encounter context|<ol/);
  assert.match(markup, new RegExp(runtimeBlockAnchor(fixture.quest_node.Key)));
});

test('flattened structural and empty nodes keep anchors, objectives and outcomes', () => {
  const data = encounter(node('wrapper', { title: 'Story activity', condition: null, children: [
    node('empty', { condition: null }), node('objective', { title: 'Protect the academy',
      completions: [{ is_success: true, phase: 'FinishActions' }, { is_success: false, phase: 'FinishActions' }] }),
  ] }));
  const markup = render(data);
  for (const id of ['wrapper', 'empty', 'objective']) assert.match(markup, new RegExp(encounterAnchor(id)));
  assert.match(markup, /Protect the academy/);
  assert.match(markup, /Encounter success condition/);
  assert.match(markup, /Encounter failure condition/);
  assert.doesNotMatch(markup, />Action<|End of authored action|<h4>Story activity/);
  assert.equal(markup.split('Only ordered steps establish a sequence.').length - 1, 1);
});

test('two empty conditional children retain source anchors without empty branches', () => {
  const data = encounter(node('empty-conditional', { title: 'Story activity', condition: null,
    children_order: 'conditional', children: [
      node('empty-a', { title: 'Story activity', condition: { Type: 'CheckConditionGroup', Flag: 17 },
        branch_condition: { Type: 'QuestFlag', Value: 'left' } }),
      node('empty-b', { title: 'Story activity', condition: null,
        branch_condition: { Type: 'QuestFlag', Value: 'right' } }),
    ] }));
  const markup = render(data);
  const [visible, evidence] = markup.split('<details class="source">');
  assert.doesNotMatch(visible, /class="branch"|class="branches"|<li|<ol|Conditional path|Runtime conditions/);
  assert.doesNotMatch(visible, /<summary>Encounter activities<\/summary>/);
  for (const id of ['empty-conditional', 'empty-a', 'empty-b']) {
    assert.equal((markup.match(new RegExp(`id="${encounterAnchor(id)}"`, 'g')) || []).length, 1);
    assert(evidence.includes(`id="${encounterAnchor(id)}"`));
  }
  for (const value of ['CheckConditionGroup', 'QuestFlag', 'left', 'right', '17']) assert(evidence.includes(value));
});

test('empty structural siblings never create list items or branch disclosures', () => {
  for (const order of ['sequence', 'conditional', 'alternative', 'parallel', 'unknown']) {
    const data = encounter(node('wrapper', { title: 'Story activity', condition: null, children_order: order,
      children: [node('empty', { condition: null }),
        node('decorator', { title: 'Story activity', condition: null, children: [
          node('objective', { title: 'Protect the academy', branch_condition: { Type: 'WaitBattleCondition', EntityId: 42 } }),
        ] }),
      ] }));
    const markup = render(data);
    const [visible, evidence] = markup.split('<details class="source">');
    assert.equal((visible.match(/<li>/g) || []).length, order === 'sequence' ? 1 : 0);
    assert.equal((visible.match(/class="branch"/g) || []).length,
      ['conditional', 'alternative', 'parallel'].includes(order) ? 1 : 0);
    assert.doesNotMatch(visible, /<li>\s*<\/li>|Conditional path|Alternative path<|Parallel path<|WaitBattleCondition|EntityId/);
    if (order === 'conditional') {
      assert.match(visible, /<summary>Protect the academy<\/summary>/);
      assert.match(visible, /runtime conditions, not player choices/);
      assert.match(visible, /order unknown/);
    }
    if (order === 'alternative') assert.match(visible, /does not identify a player choice/);
    assert(evidence.includes('WaitBattleCondition'));
    for (const id of ['wrapper', 'empty', 'decorator', 'objective'])
      assert.equal((markup.match(new RegExp(`id="${encounterAnchor(id)}"`, 'g')) || []).length, 1);
  }
});

test('component availability never claims full cinematic playback', () => {
  for (const playbackResolved of [false, true]) {
    const markup = renderToStaticMarkup(React.createElement(EncounterCinematicBoundary,
      { media: [cinematic()], componentCount: 2, playbackResolved }));
    assert.match(markup, /In-game scene · 2 video components available/);
    assert.doesNotMatch(markup, /Playback pending|Playback available|full playback/i);
  }
});

test('flow content owns video components while fallback remains available without content', () => {
  const data = encounter(node('component-owner', { flow_states: [flow('component-flow', 'Flow28', {
    sequence_media: { schema_version: 1, complete_video: false, sections: [
      { kind: 'movie_fragment', status: 'published', file_id: 33, url: 'https://cdn.test/M0334.webm' },
    ] },
  })] }));
  const content = render(data, { renderFlow: () => [React.createElement('p', { key: 'owned' }, 'Owned scene components')] });
  assert.match(content, /Owned scene components/);
  assert.doesNotMatch(content, /Video components|View video component/);
  assert.match(render(data, { renderFlow: () => [] }), /View video component 1/);
});

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
  assert.match(markup, /Parallel paths/);
  assert.equal(markup.split('Only ordered steps establish a sequence.').length - 1, 1);
  assert.equal((markup.match(/Dialogue during the encounter/g) || []).length, 4);
  assert(markup.indexOf(encounterAnchor('67')) < markup.indexOf(encounterAnchor('48')));
});

test('different LevelPlay contexts and unresolved membership never become one timeline', () => {
  const data = encounter(node('first'), { status: 'partial', trees: [
    { level_play_id: 114000004, ordering: 'authored_tree', membership: source, root: node('first') },
    { level_play_id: 114750000, ordering: 'authored_tree', membership: source, root: node('second') },
  ], unresolved_level_plays: [{ level_play_id: 100, membership: source, reason: 'invalid_authored_tree', diagnostic: 'missing_root' }] });
  const markup = render(data), visible = markup.split('<details class="source">')[0];
  assert.match(markup, /relative order of these encounter contexts is not established/);
  assert.doesNotMatch(visible, /relative order of these encounter contexts is not established/);
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
  const [visible, evidence] = markup.split('<details class="source">');
  assert.doesNotMatch(visible, /BuffTechnicalName|Before this action|After this action/);
  assert.match(evidence, /BuffTechnicalName/);
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
  assert.match(markup, /class="marker">Scene<\/div>/); assert.match(markup, /End scene/);
  const visible = markup.split('<details class="source">')[0];
  assert.match(visible, /Playback pending/);
  assert.doesNotMatch(visible, /Action boundaries|cinematic action|In-game cinematic sequence/);
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


test('published video components are actually reachable inside their flow disclosure', () => {
  const data = encounter(node('root', { flow_states: [flow('movie-flow', 'Flow28', { sequence_media: {
    schema_version: 1, complete_video: false, sections: [{ kind: 'movie_fragment', status: 'published', file_id: 33,
      url: 'https://cdn.test/M0334.webm' }],
  } })] }));
  const markup = render(data);
  assert.match(markup, /Video components · 1/);
  assert.match(markup, /View video component 1/);
  assert.match(markup, /used inside the game-rendered scene/);
  assert.doesNotMatch(markup, /<video/);
});


test('quest reader nests cinematic dialogue once and preserves cited line addresses', () => {
  const sequenceMedia = { schema_version: 1, complete_video: false, sections: [
    { kind: 'movie_fragment', status: 'published', file_id: 33, url: 'https://cdn.test/M0334.webm' },
  ] };
  const root = node('root', { condition: null, children_order: 'sequence', children: [
    node('cinema', { flow_states: [flow('cinema-occurrence', 'Flow28', { phase: 'Condition', async: false,
      timing: 'blocking', cinematics: [cinematic()], sequence_media: sequenceMedia })] }),
    node('parallel', { condition: null, children_order: 'parallel', children: [
      node('a', { flow_states: [flow('repeat-a')] }), node('b', { flow_states: [flow('repeat-b')] }),
    ] }),
  ] });
  const data = encounter(root);
  const dummy = new Proxy({}, { get: () => () => null });
  const story = loadStory('QuestRuntimeBlock');
  let sidebarGroups;
  const mocks = {
    react: React, 'lucide-react': dummy,
    '@/components/story/QuestRuntimeBlock': story,
    '@/components/story/QuestSequenceFragments': loadStory('QuestSequenceFragments'),
    '@/components/story/QuestMediaReferences': { DialogueAudioReference: ({ children }) => children, QuestMediaReferences: () => null },
    '@/components/story/QuestCutscenes': {
      Cutscene: ({ event }) => React.createElement('div', { 'data-player': event.reference,
        id: loadStory('cutsceneAnchor').cutsceneAnchor(event) }, (event.entry_points || [])
        .filter(point => loadStory('cutsceneAnchor').cutsceneAnchor(point) !== loadStory('cutsceneAnchor').cutsceneAnchor(event))
        .map(point => React.createElement('span', { key: loadStory('cutsceneAnchor').cutsceneAnchor(point),
          id: loadStory('cutsceneAnchor').cutsceneAnchor(point) }))),
      QuestCutscenes: () => null, RelatedQuestCutscenes: () => null,
    },
    '@/components/story/encounterTransitions': { encounterFlows },
    '@/components/story/cutsceneAnchor': loadStory('cutsceneAnchor'),

    '@/components/story/questReading': loadStory('questReading'),
    '@/components/story/cutscenePlacement': loadStory('cutscenePlacement'),
    '@/components/dialogue/PlayerText': { PlayerText: ({ value, fallback }) => typeof value === 'string' ? value : value?.content || fallback },
    '@/data/localized': { localizedText },
    '@/components/story/runtimeActivity': { isSourceOnlyActivity, sidebarActivityTitle },
    './questSidebarModel': { activityDestination, dialogueSearchIndex },
    './QuestSidebar': { QuestSidebar: ({ groups }) => { sidebarGroups = groups; return null; } },
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
    { id: 'before', flow_state: 'Before', story_position: [0], text: { content: 'Before the encounter' },
      action: { name: 'ShowTalk' }, source_type: 'flow_state', game_ids: { talk_item_id: 77 },
      provenance: { source_file: 'BinData/flowState/flowstate.json', source_row: 21 } },
    { id: 'dialogue:SHUTUP', flow_state: 'Flow28', action: { index: 0 }, text: { content: 'SHUT UP!' }, story_order_mapped: false,
      game_ids: { talk_item_id: 0 }, provenance: { source_file: 'BinData/flowState/flowstate.json', source_row: 0 } },
    { id: 'dialogue:THATNOTHOME', flow_state: 'Flow28', action: { index: 0 }, text: { content: 'THAT IS NOT HOME!' }, story_order_mapped: false },
    { id: 'dialogue:repeat', flow_state: 'Flow29', action: { index: 0 }, text: { content: 'A parallel call' }, story_order_mapped: false },
    { id: 'after', flow_state: 'After', story_position: [10], text: { content: 'After the encounter' } },
  ];
  const markup = renderToStaticMarkup(React.createElement(exports.QuestPageClient, {
    questKey: '114000031', initialSelection: { locale: 'en', gameVersion: '', citedLine: 'dialogue:SHUTUP' },
    initialProfile: { quest: { game_quest_id: 114000031, name: { content: 'Battle fixture' } } },
    initialTranscript: { lines, limit: 100, offset: 0, story_blocks: [
      { id: 'gate', title: 'Battle', position: [2], condition_type: 'FinishDungeon', branches: [], flow_states: [], source, encounter: data },
      { id: 'technical', title: 'Story activity', position: [1], branches: [], flow_states: [], source },
      { id: 'conversation', title: 'Continue the story', condition_type: 'PlayFlow', position: [0],
        branches: [], flow_states: [{ state: 'Before', position: [0] }], source },
    ] }, initialMedia: { events: [], related_cutscenes: [] },
  }));
  for (const id of ['dialogue:SHUTUP', 'dialogue:THATNOTHOME', 'dialogue:repeat']) {
    assert.equal(markup.split(`data-dialogue-key="${id}"`).length - 1, 1);
  }
  const publicActivities = sidebarGroups.find((group) => group.id === 'activities').entries;
  assert.equal(publicActivities.length, 2);
  assert.deepEqual(publicActivities.map((entry) => entry.id).sort(), ['conversation', 'gate']);
  assert.equal(publicActivities.find((entry) => entry.id === 'conversation').title, 'Conversation');
  assert(markup.includes(`id="${runtimeBlockAnchor('technical')}"`), 'source-only direct-link anchor remains mounted');
  const start = markup.indexOf('class="marker">Scene</div>'), end = markup.indexOf('End scene');
  assert(start >= 0 && start < markup.indexOf('SHUT UP!') && markup.indexOf('THAT IS NOT HOME!') < end, JSON.stringify({start,end,shutup:markup.indexOf('SHUT UP!'),notHome:markup.indexOf('THAT IS NOT HOME!'),excerpt:markup.slice(Math.max(0,start),Math.max(0,start)+1800)}));
  const components = '<summary>Video components · 1</summary>';
  assert.equal(markup.split(components).length - 1, 1, 'canonical flow renders one component block');
  assert.equal(markup.split('>View video component 1<').length - 1, 1);
  assert(start < markup.indexOf(components) && markup.indexOf(components) < markup.indexOf('SHUT UP!'),
    'video components belong inside Scene before its dialogue');
  const scene = markup.slice(start, end);
  assert.match(scene, /In-game scene · 1 video components available/);
  assert.doesNotMatch(scene, /Playback pending|Playback available|full playback/i);
  assert.match(markup, /Read the dialogue shared by this branch/);
  assert.match(markup, /cited-passage/);
  const articles = [...markup.matchAll(/<article\b[^>]*data-dialogue-key="[^"]+"[^>]*>([\s\S]*?)<\/article>/g)];
  assert.equal(articles.length, lines.length);
  for (const article of articles) {
    const disclosure = article[1].match(/<details class="quest-line-source"[^>]*>[\s\S]*?<\/details>/);
    assert(disclosure, 'every transcript line retains a source disclosure');
    assert.doesNotMatch(disclosure[0].split('>')[0], /\bopen\b/);
    assert.doesNotMatch(article[1].replace(disclosure[0], ''), /line-state|action-tag|line-foot/);
  }
  const regularSource = articles[0][1].match(/<details class="quest-line-source"[^>]*>[\s\S]*?<\/details>/)[0];
  for (const value of ['Before', 'ShowTalk', 'Talk 77', 'flowstate.json', 'row 21']) assert(regularSource.includes(value));
  assert.match(articles[1][1], /Talk 0/);
  assert.match(articles[1][1], /row 0/);
  const ids = [...markup.matchAll(/\bid="([^"]+)"/g)].map(match => match[1]);
  assert.equal(new Set(ids).size, ids.length, 'deep link targets must remain unique');
  const primary = { kind: 'cutscene', reference: 'cutscene:shared', flow_state: 'absent',
    action: 'first', action_index: 1, story_position: [1], source };
  const alternate = { ...primary, flow_state: 'visible', action: 'second', story_position: [5] };
  const grouped = { ...primary, entry_points: [primary, alternate] };
  const renderMedia = (windowLines, blocks, events) => renderToStaticMarkup(React.createElement(exports.QuestPageClient, {
    questKey: '114000031', initialSelection: { locale: 'en', gameVersion: '', citedLine: 'dialogue:SHUTUP' },
    initialProfile: { quest: { game_quest_id: 114000031, name: { content: 'Media fixture' } } },
    initialTranscript: { lines: windowLines, offset: 1, limit: 2, story_blocks: blocks },
    initialMedia: { events, related_cutscenes: [] },
  }));
  const fallbackMarkup = renderMedia([
    { id: 'fallback-before', flow_state: 'Before', text: { content: 'Before' }, story_position: [4] },
    { id: 'fallback-line', flow_state: 'Flow28', text: { content: 'Dialogue without cinematic parts' }, action: { index: 0 } },
    { id: 'fallback-after', flow_state: 'After', text: { content: 'After' }, story_position: [6] },
  ], [{ id: 'fallback-gate', title: 'Battle', position: [5], branches: [], flow_states: [], source,
    encounter: encounter(node('fallback-root', { flow_states: [flow('fallback-flow', 'Flow28', {
      cinematics: [], sequence_media: sequenceMedia,
    })] })) }], []);
  assert.equal(fallbackMarkup.split(components).length - 1, 1,
    'published components remain reachable when no cinematic part exists');
  assert.equal(fallbackMarkup.split('>View video component 1<').length - 1, 1);
  assert(fallbackMarkup.indexOf(components) < fallbackMarkup.indexOf('Dialogue without cinematic parts'));
  assert.doesNotMatch(fallbackMarkup, /class="marker">Scene<\/div>/);
  const assertPlayer = (html) => {
    assert.equal((html.match(/data-player="cutscene:shared"/g) || []).length, 1);
    const targets = [...html.matchAll(/\bid="([^"]+)"/g)].map(match => match[1]);
    assert.equal(new Set(targets).size, targets.length);
    for (const entry of [primary, alternate]) assert(targets.includes(loadStory('cutsceneAnchor').cutsceneAnchor(entry)));
    const scenes = sidebarGroups.find(group => group.id === 'scenes').entries;
    assert.equal(scenes.length, 1);
    assert(targets.includes(scenes[0].href.slice(1)));
  };
  assertPlayer(renderMedia([
    { id: 'a', flow_state: 'visible', text: { content: 'Before' }, story_position: [4] },
    { id: 'b', flow_state: 'visible', text: { content: 'After' }, story_position: [6] },
  ], [], [grouped]));
  const interactionBlocks = ['first', 'second'].map(id => ({ id, title: 'Inspect', position: [5], branches: [],
    flow_states: [], source, interactions: [{ id: 'inspect', title: 'Inspect', source, flow_states: [] }] }));
  assertPlayer(renderMedia([
    { id: 'a', flow_state: 'visible', text: { content: 'Before' }, story_position: [4] },
    { id: 'b', flow_state: 'visible', text: { content: 'After' }, story_position: [6] },
  ], interactionBlocks, [{ ...grouped, interaction_id: 'absent:inspect', entry_points: [
    { ...primary, interaction_id: 'first:inspect' }, { ...alternate, interaction_id: 'second:inspect' },
  ] }]));

  const sharedScene = cinematic({ kind: 'cutscene', reference: 'cutscene:shared' });
  const sharedEncounter = encounter(node('shared-root', { flow_states: [
    flow('shared-a', 'Flow28', { cinematics: [sharedScene] }),
    flow('shared-b', 'Flow29', { cinematics: [sharedScene] }),
  ] }));
  assertPlayer(renderMedia([
    { id: 'before-shared', flow_state: 'before', text: { content: 'Before' }, story_position: [4] },
    { id: 'shared-a-line', flow_state: 'Flow28', text: { content: 'First call' }, action: { index: 0 } },
    { id: 'shared-b-line', flow_state: 'Flow29', text: { content: 'Second call' }, action: { index: 0 } },
    { id: 'after-shared', flow_state: 'after', text: { content: 'After' }, story_position: [6] },
  ], [{ id: 'shared-gate', title: 'Battle', position: [5], branches: [], flow_states: [], source,
    encounter: sharedEncounter }], [{ ...grouped, transcript_states: ['Flow28', 'Flow29'] }]));

});
