import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const require = createRequire(import.meta.url);
function load(name, mocks = {}) {
  const exports = {};
  const source = readFileSync(new URL(`../src/components/story/${name}`, import.meta.url), 'utf8');
  const code = ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true,
  } }).outputText;
  vm.runInNewContext(code, { exports, require: id => mocks[id] ?? require(id) });
  return exports;
}
const preferred = load('preferredRover.ts');
const timeline = load('cutsceneTimeline.ts', { './preferredRover': preferred });
const flow = {
  version: 1, entry: 'intro', asset_version: '3.7.1', evidence: 'test',
  nodes: [{ id: 'intro', kind: 'clip', asset: 'a', start: 0, end: null, next: null }],
  media: { a: { url: 'a.mp4' } },
};

test('published duration unlocks a timeline before browser metadata arrives', () => {
  const value = { ...flow, media: { a: { url: 'a.mp4', duration_seconds: 94.5 } } };
  const result = timeline.cutsceneTimeline(value, {}, 'ask', {});
  assert.equal(result.complete, true);
  assert.equal(result.total, 94.5);
  assert.equal(timeline.timelineTarget(result, 45).time, 45);
  assert.equal(timeline.cutsceneTimeline(value, {}, 'ask', { a: 95 }).total, 95);
  assert.equal(timeline.cutsceneTimeline({ ...value, nodes: [{ ...flow.nodes[0], end: 20 }] }, {}, 'ask', {}).total, 20);
});

function player(value) {
  const states = [], refs = [];
  let stateIndex = 0, refIndex = 0;
  const components = load('QuestCutscenes.tsx', {
    react: {
      useState(initial) {
        const i = stateIndex++;
        if (!(i in states)) states[i] = initial;
        return [states[i], update => { states[i] = typeof update === 'function' ? update(states[i]) : update; }];
      },
      useRef(initial) { const i = refIndex++; return refs[i] ??= { current: initial }; },
      useCallback: fn => fn, useMemo: fn => fn(), useEffect() {},
    },
    '../../data/gameText': { gameTextPlainText: v => v },
    '../../config/settings': { APP_SETTINGS: { cutscene: { masterVolume: .7, musicLevel: .3 } } },
    '../../preferences/NarrativePreferences': { useNarrativePreferences: () => ({ preferredRover: 'ask', voiceLanguage: 'en' }) },
    '../dialogue/PlayerText': { PlayerText: 'text' },
    './AnalysisActions': { AnalysisActions: 'actions' },
    './cutsceneAnchor': {}, './CutsceneControls': { CutsceneControls: 'controls' },
    './CutsceneSound': { CutsceneSound: 'sound' },
    './cutsceneAudioSelection': { selectCutsceneTracks: () => [], clipRover: () => 'female' },
    './cutsceneTimeline': timeline, './preferredRover': preferred,
    '@/lib/router': { Link: 'a' },
  });
  return () => {
    stateIndex = refIndex = 0;
    const tree = components.FlowPlayer({ flow: value, title: 'Test' });
    const videos = [];
    let controls;
    function visit(node) {
      if (!node || typeof node !== 'object') return;
      if (node.type === 'video') videos.push(node);
      if (node.type === 'controls') controls = node.props;
      for (const child of [node.props?.children].flat(Infinity)) visit(child);
    }
    visit(tree);
    return { videos, controls };
  };
}

test('late durationchange recovers timeline and seeking after initially unknown metadata', () => {
  const render = player(flow);
  let view = render();
  assert.equal(view.videos.length, 1); // No duplicate probe of the active video.
  const video = { duration: Infinity, currentTime: 0, readyState: 1, paused: true, pause() {} };
  view.videos[0].props.ref.current = video;
  view.videos[0].props.onLoadedMetadata({ currentTarget: video });
  assert.equal(render().controls.timeline.complete, false);
  video.duration = 94.5;
  view.videos[0].props.onDurationChange({ currentTarget: video });
  view = render();
  assert.equal(view.controls.timeline.complete, true);
  assert.equal(view.controls.timeline.total, 94.5);
  view.controls.onSeek(45);
  assert.equal(video.currentTime, 45);
  for (const duration of [0, NaN, Infinity]) {
    video.duration = duration;
    view.videos[0].props.onDurationChange({ currentTarget: video });
    assert.equal(render().controls.timeline.total, 94.5);
  }
});

test('late metadata for a later clip unlocks the complete multi-clip timeline', () => {
  const render = player({ ...flow,
    nodes: [{ ...flow.nodes[0], next: 'tail' }, { id: 'tail', kind: 'clip', asset: 'b', start: 0, end: null, next: null }],
    media: { a: { url: 'a.mp4', duration_seconds: 10 }, b: { url: 'b.mp4' } },
  });
  const view = render();
  const probe = view.videos.find(v => v.props.hidden);
  assert.ok(probe);
  assert.equal(view.controls.timeline.complete, false);
  probe.props.onLoadedMetadata({ currentTarget: { duration: Infinity } });
  probe.props.onDurationChange({ currentTarget: { duration: 25 } });
  assert.equal(render().controls.timeline.total, 35);
  assert.equal(render().controls.timeline.complete, true);
});
