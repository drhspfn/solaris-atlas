import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import test from 'node:test';
import vm from 'node:vm';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import ts from 'typescript';

const require = createRequire(import.meta.url);
const source = readFileSync(new URL('../src/components/story/QuestIllustration.tsx', import.meta.url), 'utf8');
const code = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
const exports = {};
const mocks = {
  '../../preferences/NarrativePreferences': { useNarrativePreferences: () => ({ preferredRover: 'ask' }) },
  '../ui/Select': { Select: () => null },
  '../ui/RenderedAnimation': { RenderedAnimation: ({ fallback }) => fallback },
  '../ui/animationPreview': { selectAnimationClip: () => null },
  './QuestAnimatedBackdrop': { QuestAnimatedBackdrop: ({ variant }) => React.createElement('div', { 'data-animation': variant.animation }) },
};
vm.runInNewContext(code, { exports, require: (id) => mocks[id] ?? require(id) });
const { QuestIllustration } = exports;
function render(playback, images = [{ rover: null, engine_path: '/Game/Poster', url: 'https://cdn.test/poster.png' }]) {
  return renderToStaticMarkup(React.createElement(QuestIllustration, {
    illustration: { id: 'scene', kind: 'spine', animation: 'idle', images,
      bundle: { url: 'https://cdn.test/source.zip', playback } },
  }));
}

for (const playback of [undefined, null, { status: 'unsupported' }, { variants: null }, { variants: [] }]) {
  test(`archived scene without playable variants preserves its poster: ${JSON.stringify(playback)}`, () => {
    assert.match(render(playback), /https:\/\/cdn.test\/poster.png/);
  });
}

test('an unsupported scene without a poster renders its unavailable state instead of crashing SSR', () => {
  assert.match(render({ status: 'unsupported' }, []), /playback unavailable/);
});

test('a supported authored animation still renders through the animated backdrop', () => {
  const markup = render({ variants: [{ rover: null, animation: 'idle', scene_url: 'https://cdn.test/scene.json', textures: {} }] });
  assert.match(markup, /data-animation="idle"/);
  assert.doesNotMatch(markup, /src="https:\/\/cdn.test\/poster.png"/);
});
