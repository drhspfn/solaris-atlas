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
function loadUi(name) {
  const key = `ui/${name}`;
  if (cache.has(key)) return cache.get(key);
  let path = new URL(`../src/components/ui/${name}.tsx`, import.meta.url);
  if (!existsSync(path)) path = new URL(`../src/components/ui/${name}.ts`, import.meta.url);
  const code = ts.transpileModule(readFileSync(path, 'utf8'), { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX,
  } }).outputText;
  const exports = {}; cache.set(key, exports);
  vm.runInNewContext(code, { exports, URL, require: id => {
    if (id.endsWith('.css')) return {};
    if (id === 'lucide-react') return new Proxy({}, { get: () => () => null });
    if (id.startsWith('./')) return loadUi(id.slice(2));
    return require(id);
  } });
  return exports;
}
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
    if (id === '../ui/AudioPlayer' || id === '../ui/Select') return loadUi(id.split('/').at(-1));
    if (id.startsWith('./')) return loadStory(id.slice(2));
    return require(id);
  } });
  return exports;
}
const { QuestEncounter, EncounterCinematicBoundary } = loadStory('QuestEncounter');
const { QuestSequenceFragments, playableEncounterFragments } = loadStory('QuestSequenceFragments');
const { QuestSceneDetails } = loadStory('QuestSceneDetails');
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
    assert.doesNotMatch(visible, /Only ordered steps|runtime conditions|player choice|order unknown/i);
    assert.match(markup, /<details class="source"><summary>Source &amp; conditions/);
    assert.match(evidence, /Only ordered steps establish a sequence/);
    if (order === 'conditional') {
      assert.match(visible, /<summary>Protect the academy<\/summary>/);
      assert.match(visible, /Conditional events/);
      assert.match(evidence, /runtime conditions, not player choices/);
    }
    if (order === 'alternative') {
      assert.match(visible, /Alternative events/);
      assert.match(evidence, /does not identify a player choice/);
    }
    if (order === 'parallel') assert.match(visible, /Parallel activities/);
    assert(evidence.includes('WaitBattleCondition'));
    for (const id of ['wrapper', 'empty', 'decorator', 'objective'])
      assert.equal((markup.match(new RegExp(`id="${encounterAnchor(id)}"`, 'g')) || []).length, 1);
  }
});

test('branch groups show plain labels and objectives while explanations stay in closed Source', () => {
  for (const [order, label] of [['conditional', 'Conditional events'], ['alternative', 'Alternative events'],
    ['parallel', 'Parallel activities'], ['unknown', 'Related activities']]) {
    const markup = render(encounter(node('group', { title: 'Story activity', condition: null,
      children_order: order, children: [node('protect', { title: 'Protect the academy' }),
        node('leave', { title: 'Leave the area' })] })));
    const [visible, evidence] = markup.split('<details class="source">');
    assert(visible.includes(`aria-label="${label}"`));
    for (const title of ['Protect the academy', 'Leave the area'])
      assert(visible.includes(`<summary>${title}</summary>`));
    assert.doesNotMatch(visible, /Only ordered steps|runtime conditions|player choice|order unknown/i);
    assert.match(evidence, /Only ordered steps establish a sequence/);
    assert.match(evidence, /WaitBattleCondition/);
  }
});

test('component availability never claims full cinematic playback', () => {
  for (const playbackResolved of [false, true]) {
    const markup = renderToStaticMarkup(React.createElement(EncounterCinematicBoundary,
      { media: [cinematic()], componentCount: 2, playbackResolved }));
    assert.match(markup, /Scene · 2 video components available/);
    assert.doesNotMatch(markup, /Playback pending|Playback available/);
    assert.match(markup, /Full scene playback is not available here/);
  }
});

test('missing recorded video is not mislabeled as unsupported engine rendering', () => {
  const markup = renderToStaticMarkup(React.createElement(EncounterCinematicBoundary,
    { media: [cinematic({ kind: 'cutscene' })] }));
  assert.match(markup, /class="marker">Cutscene<\/div>/);
  assert.match(markup, /No video export has been published/);
  assert.doesNotMatch(markup, /rendered by the game engine|cannot currently be reconstructed/);
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
  assert.match(markup, /Parallel activities/);
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
  assert.match(markup, /class="marker">Scripted scene<\/div>/);
  const visible = markup.split('<details class="source">')[0];
  assert.match(visible, /No playable scene recording is available/);
  assert.doesNotMatch(visible, /rendered by the game engine/);
  assert.doesNotMatch(visible, /Playback pending|queued|End scene/i);
  assert.doesNotMatch(visible, /Full scene playback is not available here/);
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
  assert.deepEqual(Array.from(playable, p => p.file_id), [7, 10]); assert.equal(JSON.stringify(media), before);
  const tree = allNodes(QuestSequenceFragments({ media })).find(node => Array.isArray(node.props?.items));
  assert.equal(tree.props.items.length, 2);
  assert.equal(tree.props.items[0].src, 'https://cdn.test/M0334.webm');
  assert.equal(tree.props.items[0].start, undefined); assert.equal(tree.props.items[0].end, undefined);
  const list = tree.type(tree.props);
  allNodes(list).find(n => n.props?.onClick).props.onClick();
  assert.equal(opened.at(-1)[0][0].src, 'https://cdn.test/M0334.webm');
  assert.match(renderToStaticMarkup(React.createElement(QuestSequenceFragments, { media: { ...media, sections: [] } })), /illustrations have not been rendered/);
});

test('scene cards describe actual available media for placeholder and illustrated sequences', () => {
  const scene = (sequence_media, title) => renderToStaticMarkup(React.createElement(QuestSceneDetails, {
    event: { kind: 'sequence', title, sequence_media, source, reference: 'asset:scene' }, display,
  }));
  const placeholder = { schema_version: 1, complete_video: false, sections: [],
    composition: { schema_version: 1, state: 'unsupported', reasons: [{ code: 'flow_owner_unavailable' }] } };
  for (const media of [undefined, placeholder, { ...placeholder, composition: undefined }]) {
    const markup = scene(media);
    assert.match(markup, /Scripted scene/);
    assert.match(markup, /No standalone media available/);
    assert.match(markup, /visual format is unknown/);
    assert.doesNotMatch(markup, /Story scene|rendering failed|queued|Explore the artwork/);
  }
  const illustrated = { ...placeholder, sections: [{ kind: 'illustration', status: 'published',
    file_id: 1, url: 'https://cdn.test/scene.png' }] };
  assert.match(scene(illustrated), /Illustrated scene/);
  assert.match(scene(illustrated), /Scene illustrations available/);
  assert.doesNotMatch(scene(illustrated), /No standalone media available/);
  assert.match(scene({ ...illustrated, sections: [{ ...illustrated.sections[0], url: 'javascript:alert(1)' }] }), /No standalone media available/);
  assert.match(scene(placeholder, 'A named scene'), /A named scene/);
  const fragment = { ...placeholder, sections: [{ kind: 'movie_fragment', status: 'published',
    file_id: 2, url: 'https://cdn.test/fragment.webm' }] };
  assert.match(scene(fragment), /Cinematic scene/);
  assert.doesNotMatch(scene(fragment), /No standalone media available/);
  assert.match(scene(soundtrackMedia()), /Scripted scene/);
  assert.doesNotMatch(scene(soundtrackMedia()), /No standalone media available/);
  for (const kind of ['skeletal_animation', 'skeletal_animation_asset']) {
    const engine = { ...placeholder, sections: [{ kind: 'camera' }, { kind }] };
    assert.match(scene(engine), /In-game cinematic/);
    assert.match(scene(engine), /Rendered by the game engine\. No standalone recording is available/);
    for (const section of engine.sections) {
      assert.match(scene({ ...engine, sections: [section] }), /Scripted scene/);
      assert.doesNotMatch(scene({ ...engine, sections: [section] }), /Rendered by the game engine/);
    }
    const mixed = { ...engine, sections: [...engine.sections, ...illustrated.sections] };
    assert.match(scene(mixed), /Illustrated scene/);
    assert.doesNotMatch(scene(mixed), /Rendered by the game engine/);
    assert.match(scene({ ...engine, sections: [...engine.sections, ...fragment.sections] }), /Cinematic scene/);
  }
});

test('open scene information describes available media and retains source details', () => {
  const code = ts.transpileModule(readFileSync(new URL('../src/components/story/QuestSceneDetails.tsx', import.meta.url), 'utf8'),
    { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS,
      jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } }).outputText;
  const exports = {};
  vm.runInNewContext(code, { exports, require: id => {
    if (id === 'react') return { ...React, useState: () => [true, () => {}] };
    if (id === 'lucide-react') return new Proxy({}, { get: () => () => null });
    if (id.endsWith('.module.css')) return { __esModule: true, default: new Proxy({}, { get: (_, key) => String(key) }) };
    if (id === '../dialogue/PlayerText') return { PlayerText: ({ value, fallback }) => value || fallback };
    if (id === '../ui/Button') return { Button: ({ children }) => React.createElement('button', null, children) };
    if (id === '../ui/mediaViewerUtils') return { mediaViewerUrl };
    if (id.startsWith('./')) return loadStory(id.slice(2));
    return require(id);
  } });
  const renderScene = sequence_media => renderToStaticMarkup(React.createElement(exports.QuestSceneDetails, {
    event: { kind: 'sequence', sequence_media, source, reference: 'asset:scene' }, display,
  }));
  const placeholder = { schema_version: 1, complete_video: false, sections: [],
    composition: { schema_version: 1, state: 'unsupported', reasons: [{ code: 'flow_owner_unavailable' }] } };
  const empty = renderScene(placeholder);
  assert.match(empty, /No standalone media available. Source details are shown below/);
  assert.match(empty, /Scene illustrations are unavailable/);
  assert.doesNotMatch(empty, /required assets are missing|rendering failed/);
  assert.match(empty, /<details class="source" open=""><summary>Technical details/);
  assert.match(empty, /asset:scene/);
  assert.match(empty, /private-source\.json/);
  assert.match(empty, /\$\.Data/);
  assert.doesNotMatch(empty, /Explore the artwork|Select an image/);
  const unbound = renderScene({ ...placeholder, composition: { ...placeholder.composition,
    reasons: [{ code: 'scene_binding_unavailable' }] } });
  assert.match(unbound, /Scene illustrations are unavailable/);
  assert.doesNotMatch(unbound, /required assets are missing|rendering failed/);
  const failed = renderScene({ ...placeholder, composition: { schema_version: 1, state: 'failed', reasons: [] } });
  assert.match(failed, /Illustration rendering failed/);
  const unknown = renderScene({ ...placeholder, composition: undefined });
  assert.doesNotMatch(unknown, /rendering failed|queued/);
  const engine = renderScene({ ...placeholder, sections: [{ kind: 'camera' }, { kind: 'skeletal_animation' }] });
  assert.match(engine, /<p class="dialogIntro">Rendered by the game engine\. No standalone recording is available\.<\/p>/);
  assert.match(engine, /<details class="source" open=""><summary>Technical details/);
  const visual = renderScene({ ...placeholder, sections: [{ kind: 'illustration', status: 'published',
    file_id: 1, url: 'https://cdn.test/scene.png' }] });
  assert.match(visual, /Available scene media and source details/);
  assert.match(visual, /Open Scene illustration 1/);
  assert.match(visual, /<details class="source"><summary>Technical details/);
});


test('component display order uses earliest occurrence before deduplicating files', () => {
  const part = (file_id, start) => ({ kind: 'movie_fragment', status: 'published', file_id,
    url: `https://cdn.test/${file_id}.webm`, scene_source: 'same-scene',
    clock: { numerator: 30, denominator: 1 }, range: { Value: {
      LowerBound: { Type: 1, Value: { Value: start * 30 } },
      UpperBound: { Type: 0, Value: { Value: (start + 1) * 30 } },
    } } });
  const media = { schema_version: 1, complete_video: false,
    sections: [part(7, 10), part(8, 5), part(7, 0)] };
  const before = JSON.stringify(media);
  assert.deepEqual(Array.from(playableEncounterFragments(media), part => part.file_id), [7, 8]);
  assert.equal(JSON.stringify(media), before);
});

const soundtrackTrack = (overrides = {}) => ({ file_id: 41, url: 'https://cdn.test/M0334-en-male.ogg',
  language: 'en', rover: 'male', status: 'published', file: 'audio/M0334-en-male.ogg', ...overrides });
const soundtrackMedia = (tracks = [soundtrackTrack()]) => ({ schema_version: 1, complete_video: false, sections: [
  { kind: 'audio_event', source: '/Game/M0334.AudioSection', reference: '/Game/Wwise/M0334',
    clock: { numerator: 30, denominator: 1 },
    range: { Value: { LowerBound: { Type: 1, Value: { Value: 120 } }, UpperBound: { Type: 0, Value: { Value: 900 } } } }, tracks },
] });

test('audio-only M0334 renders the scene soundtrack without exposing technical metadata or autoplay', () => {
  const media = soundtrackMedia();
  const before = JSON.stringify(media);
  const markup = renderToStaticMarkup(React.createElement(QuestSequenceFragments, { media }));
  assert.equal((markup.match(/<audio\b/g) || []).length, 1);
  assert.match(markup, /aria-label="Play Scene soundtrack"/);
  assert.match(markup, /src="https:\/\/cdn.test\/M0334-en-male.ogg" preload="none"/);
  assert.doesNotMatch(markup, /autoplay|<select|Soundtrack language|Soundtrack Rover|Video components|#t=/i);
  assert.match(markup, /Scene soundtrack/);
  assert.doesNotMatch(markup, /audio\/M0334-en-male\.ogg|\/Game\/M0334\.AudioSection|LowerBound|numerator|Whole Wwise event/);
  assert.equal(JSON.stringify(media), before);
  const combined = renderToStaticMarkup(React.createElement(QuestSequenceFragments, { media: {
    ...media, sections: [...media.sections, { kind: 'movie_fragment', status: 'published', file_id: 33,
      url: 'https://cdn.test/M0334.webm' }],
  } }));
  assert.match(combined, /Video components · 1/);
  assert.equal((combined.match(/<audio\b/g) || []).length, 1);
});

test('null-language soundtrack remains Shared without inventing an English language', () => {
  const shared = soundtrackTrack({ language: null, rover: null, url: 'https://cdn.test/M0334-shared.ogg',
    file: 'audio/M0334-shared.ogg' });
  const renderAudio = (tracks) => renderToStaticMarkup(React.createElement(QuestSequenceFragments, { media: soundtrackMedia(tracks) }));
  const markup = renderAudio([shared]);
  assert.match(markup, /Shared soundtrack/);
  assert.match(markup, /src="https:\/\/cdn.test\/M0334-shared.ogg"/);
  assert.doesNotMatch(markup, /English|Soundtrack language|Soundtrack Rover|<select|autoplay/);
  const english = renderAudio([shared, soundtrackTrack()]);
  assert.match(english, /src="https:\/\/cdn.test\/M0334-en-male.ogg"/);
  assert.match(english, /Shared/);
  assert.match(english, /Soundtrack language/);
  const firstActual = renderAudio([shared, soundtrackTrack({ language: 'ja', url: 'https://cdn.test/ja.ogg' })]);
  assert.match(firstActual, /src="https:\/\/cdn.test\/M0334-shared.ogg"/);
  assert.doesNotMatch(firstActual, /English/);
});

test('soundtrack selectors expose only published languages and actual Rover alternatives', () => {
  const renderAudio = (tracks) => renderToStaticMarkup(React.createElement(QuestSequenceFragments, { media: soundtrackMedia(tracks) }));
  const variants = [soundtrackTrack(), soundtrackTrack({ file_id: 42, rover: 'female', url: 'https://cdn.test/female.ogg' }),
    soundtrackTrack({ file_id: 43, language: 'ja', rover: null, url: 'https://cdn.test/ja.ogg' }),
    soundtrackTrack({ file_id: 44, language: 'ko', status: 'pending' })];
  const markup = renderAudio(variants);
  assert.match(markup, /Soundtrack language/);
  assert.match(markup, /Soundtrack Rover/);
  assert.doesNotMatch(markup, /<select|한국어/);
  assert.equal((markup.match(/<audio\b/g) || []).length, 1);
  const singleRover = renderAudio([variants[0], variants[2]]);
  assert.match(singleRover, /Soundtrack language/);
  assert.doesNotMatch(singleRover, /Soundtrack Rover/);
  for (const track of [soundtrackTrack({ status: 'pending' }), soundtrackTrack({ url: 'javascript:alert(1)' }),
    soundtrackTrack({ file: 'audio/../wrong.ogg' })]) assert.doesNotMatch(renderAudio([track]), /<audio\b/);
});

test('soundtrack selector changes choose one matching whole event without inventing Rover variants', () => {
  const states = [];
  let stateIndex = 0;
  const exports = {};
  const code = ts.transpileModule(readFileSync(new URL('../src/components/story/QuestSequenceAudio.tsx', import.meta.url), 'utf8'), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  vm.runInNewContext(code, { exports, require: id => {
    if (id === 'react') return { useState: initial => {
      const index = stateIndex++;
      if (!(index in states)) states[index] = initial;
      return [states[index], value => { states[index] = value; }];
    } };
    if (id === '../ui/mediaViewerUtils') return { mediaViewerUrl };
    if (id.startsWith('../ui/')) return loadUi(id.split('/').at(-1));
    return id.endsWith('.css') ? { default: {} } : require(id);
  } });
  const media = soundtrackMedia([soundtrackTrack(),
    soundtrackTrack({ file_id: 42, rover: 'female', url: 'https://cdn.test/female.ogg' }),
    soundtrackTrack({ file_id: 43, language: 'ja', rover: null, url: 'https://cdn.test/ja.ogg' })]);
  const renderPlayer = () => {
    stateIndex = 0;
    const scene = exports.QuestSequenceAudio({ media }).props.children[0];
    return scene.type(scene.props).props.children.find(child => child.type === loadUi('AudioPlayer').AudioPlayer);
  };
  const initial = renderPlayer();
  initial.props.settings.props.children.find(child => child.props?.label === 'Soundtrack Rover').props.onChange('female');
  assert.equal(renderPlayer().props.src, 'https://cdn.test/female.ogg');
  initial.props.settings.props.children.find(child => child.props?.label === 'Soundtrack language').props.onChange('ja');
  const japanese = renderPlayer();
  assert.equal(japanese.props.src, 'https://cdn.test/ja.ogg');
  assert.equal(japanese.props.settings.props.children.filter(Boolean).length, 1);
  assert.equal(japanese.props.label, 'Scene soundtrack');
});

test('published video components are actually reachable inside their flow disclosure', () => {
  const data = encounter(node('root', { flow_states: [flow('movie-flow', 'Flow28', { sequence_media: {
    schema_version: 1, complete_video: false, sections: [{ kind: 'movie_fragment', status: 'published', file_id: 33,
      url: 'https://cdn.test/M0334.webm' }],
  } })] }));
  const markup = render(data);
  assert.match(markup, /Video components · 1/);
  assert.match(markup, /View video component 1/);
  assert.match(markup, /files are associated with the scene/);
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
      externalSceneContinuations: (continuations = []) => continuations,
    },
    '@/components/story/encounterTransitions': { encounterFlows },
    '@/components/story/cutsceneAnchor': loadStory('cutsceneAnchor'),

    '@/components/story/questReading': loadStory('questReading'),
    '@/components/story/sequenceReader': loadStory('sequenceReader'),
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
  const start = markup.indexOf('class="marker">Scripted scene</div>'), end = markup.indexOf('<details class="source">', start);
  assert(start >= 0 && start < markup.indexOf('SHUT UP!') && markup.indexOf('THAT IS NOT HOME!') < end, JSON.stringify({start,end,shutup:markup.indexOf('SHUT UP!'),notHome:markup.indexOf('THAT IS NOT HOME!'),excerpt:markup.slice(Math.max(0,start),Math.max(0,start)+1800)}));
  const components = '<summary>Video components · 1</summary>';
  assert.equal(markup.split(components).length - 1, 1, 'canonical flow renders one component block');
  assert.equal(markup.split('>View video component 1<').length - 1, 1);
  assert(start < markup.indexOf(components) && markup.indexOf(components) < markup.indexOf('SHUT UP!'),
    'video components belong inside Scene before its dialogue');
  const scene = markup.slice(start, end);
  assert.match(scene, /Scene · 1 video components available/);
  assert.doesNotMatch(scene, /Playback pending|Playback available/i);
  assert.match(scene, /Full scene playback is not available here/);
  assert.match(markup, /Read the dialogue shared by this branch/);
  assert.match(markup, /cited-passage/);
  const articles = [...markup.matchAll(/<article\b[^>]*data-dialogue-key="[^"]+"[^>]*>([\s\S]*?)<\/article>/g)];
  assert.equal(articles.length, lines.length);
  for (const article of articles) {
    assert.doesNotMatch(article[1], /quest-line-source|line-state|action-tag|line-foot|flowstate\.json|Talk 77|row 21/);
  }
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
  renderMedia(lines, [], [
    { ...primary, reference: 'cutscene:direct', flow_state: 'Before', story_position: undefined, story_order_mapped: false },
    { ...primary, reference: 'cutscene:context', flow_state: 'Before', story_position: undefined, context_inferred: true, story_order_mapped: false },
    { ...primary, reference: 'cutscene:mapped', flow_state: 'Before', story_position: [1], story_order_mapped: true },
  ]);
  const sceneEntries = sidebarGroups.find((group) => group.id === 'scenes').entries;
  assert.deepEqual(Array.from(sceneEntries, (entry) => entry.note), [
    'Position not mapped', 'Possible connection · position not mapped', undefined,
  ]);
  for (const withBoundary of [true, false]) {
    const audioMarkup = renderMedia(lines, [{ id: 'soundtrack-gate', title: 'Battle', position: [2],
      branches: [], flow_states: [], source, encounter: encounter(node('soundtrack-root', { flow_states: [
        flow('soundtrack-canonical', 'Flow28', { phase: 'Condition', cinematics: withBoundary ? [cinematic()] : [],
          sequence_media: soundtrackMedia() }),
        flow('soundtrack-repeated', 'Flow28', { phase: 'Condition', cinematics: withBoundary ? [cinematic()] : [],
          sequence_media: soundtrackMedia() }),
      ] })) }], []);
    const audioPosition = audioMarkup.indexOf('<audio');
    assert.equal((audioMarkup.match(/<audio\b/g) || []).length, 1, 'shared Flow28 has exactly one soundtrack');
    assert.match(audioMarkup, /aria-label="Play Scene soundtrack"/);
    assert(audioPosition >= 0 && audioPosition < audioMarkup.indexOf('SHUT UP!'));
    for (const text of ['SHUT UP!', 'THAT IS NOT HOME!']) assert(audioMarkup.includes(text));
    assert.doesNotMatch(audioMarkup, /autoplay|Video components/);
    if (withBoundary) {
      const marker = audioMarkup.indexOf('class="marker">Scripted scene</div>');
      assert(marker >= 0 && marker < audioPosition);
      assert(audioMarkup.indexOf('THAT IS NOT HOME!') < audioMarkup.indexOf('<details class="source">', marker));
      assert.doesNotMatch(audioMarkup, /End scene/);
    }
  }
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
  assert.doesNotMatch(fallbackMarkup, /class="marker">Scripted scene<\/div>/);
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
