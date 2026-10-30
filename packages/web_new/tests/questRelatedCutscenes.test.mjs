import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const require = createRequire(import.meta.url);
const source = readFileSync(
  new URL('../src/components/story/QuestMediaReferences.tsx', import.meta.url),
  'utf8',
);
const code = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    jsx: ts.JsxEmit.ReactJSX,
    esModuleInterop: true,
  },
}).outputText;
const exports = {};
const mocks = {
  '../ui/Select': { Select: () => null },
  'lucide-react': new Proxy({}, { get: () => 'icon' }),
  '@/lib/router': { Link: 'atlas-link' },
  '../../preferences/NarrativePreferences': {},
  './cutsceneAnchor': {
    cutsceneAnchor: () => 'scene',
    packageCutsceneAnchor: (name) => `package-${name}`,
  },
};
vm.runInNewContext(code, {
  exports,
  require: (id) => mocks[id] ?? require(id),
});

test('scene navigation does not report an intentional audio interruption as a failed track', async () => {
  for (const name of ['AbortError', 'NotSupportedError']) {
    const updates = [];
    const audio = { paused: true, play: () => Promise.reject({ name }) };
    const components = {};
    vm.runInNewContext(code, {
      exports: components,
      document: { querySelectorAll: () => [] },
      require: (id) =>
        id === 'react'
          ? {
              useRef: () => ({ current: audio }),
              useState: (initial) => [initial, (value) => updates.push(value)],
            }
          : id === '../../preferences/NarrativePreferences'
            ? {
                useNarrativePreferences: () => ({ voiceLanguage: 'en', preferredRover: 'ask' }),
              }
            : (mocks[id] ?? require(id)),
    });
    const tree = components.DialogueAudioReference({
      media: {
        voice_references: [
          {
            source: { file: 'PlotAudio' },
            tracks: [{ language: 'en', rover: null, url: 'voice.wav' }],
          },
        ],
      },
    });
    let play;
    function visit(node) {
      if (!node || typeof node !== 'object') return;
      if (node.props?.['aria-label'] === 'Play voice') play = node;
      for (const child of [node.props?.children].flat(Infinity)) visit(child);
    }
    visit(tree);
    assert.ok(play);
    play.props.onClick();
    await Promise.resolve();
    assert.deepEqual(updates, name === 'AbortError' ? [] : ['voice.wav']);
  }
});

test('package-linked scenes remain navigable without a claimed transcript position', () => {
  const tree = exports.QuestMediaReferences({
    manifest: {
      game_version: '3.3.0',
      events: [],
      video_packages: [],
      related_cutscenes: [{ name: 'C0028', playable: false, context_inferred: true }],
    },
    stateAnchors: new Map(),
  });
  const links = [];
  const text = [];
  function visit(node) {
    if (typeof node === 'string') text.push(node);
    if (!node || typeof node !== 'object') return;
    if (node.type === 'a') links.push(node.props.href);
    for (const child of [node.props?.children].flat(Infinity)) visit(child);
  }
  visit(tree);
  assert.deepEqual(links, ['#package-C0028']);
  assert.ok(text.some((value) => value.includes('story position is not yet mapped')));
  assert.ok(text.some((value) => value.includes('Video not available yet')));
});

test('a contextual scene appears once and jumps to the scene inside the quest', () => {
  const event = {
    kind: 'cutscene',
    reference: 'cutscene:M0366',
    flow_state: 'Cave_1_39',
    action_index: 1,
    context_inferred: true,
  };
  const tree = exports.QuestMediaReferences({
    manifest: {
      events: [event],
      video_packages: [],
      related_cutscenes: [{ name: 'M0366', playable: false, context_inferred: true }],
    },
    stateAnchors: new Map(),
    visibleCutscenes: [event],
  });
  const links = [];
  function visit(node) {
    if (!node || typeof node !== 'object') return;
    if (node.type === 'a') links.push(node.props.href);
    for (const child of [node.props?.children].flat(Infinity)) visit(child);
  }
  visit(tree);
  assert.deepEqual(links, ['#scene']);
});

test('unavailable and package-linked scenes have actual in-page targets', () => {
  const componentSource = readFileSync(
    new URL('../src/components/story/QuestCutscenes.tsx', import.meta.url),
    'utf8',
  );
  const componentCode = ts.transpileModule(componentSource, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      jsx: ts.JsxEmit.ReactJSX,
      esModuleInterop: true,
    },
  }).outputText;
  const components = {};
  vm.runInNewContext(componentCode, {
    exports: components,
    require: (id) => {
      if (id === './cutsceneAnchor') return mocks[id];
      if (id === '@/lib/router') return { Link: 'a' };
      if (id.startsWith('.') || id.startsWith('@/')) return {};
      return require(id);
    },
  });
  const React = require('react');
  const { renderToStaticMarkup } = require('react-dom/server');
  const related = renderToStaticMarkup(
    React.createElement(components.RelatedQuestCutscenes, {
      manifest: {
        game_version: '3.3.0',
        events: [],
        video_packages: [],
        related_cutscenes: [{ name: 'C0028', playable: false, context_inferred: true }],
      },
    }),
  );
  assert.match(related, /id="package-C0028"/);
  assert.match(related, /Video not available yet/);
  const contextual = renderToStaticMarkup(
    React.createElement(components.Cutscene, {
      event: {
        kind: 'cutscene',
        reference: 'cutscene:M0366',
        flow_state: 'Cave_1_39',
        action_index: 1,
        context_inferred: true,
      },
    }),
  );
  assert.match(contextual, /id="scene"/);
  assert.match(contextual, /M0366.*Video not available yet/);
});
