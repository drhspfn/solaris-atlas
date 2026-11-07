import { skeletonComponents } from './skeletonComponents.mjs';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import test from 'node:test';
import vm from 'node:vm';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import ts from 'typescript';

const require = createRequire(import.meta.url);
function load(path, mocks = {}) {
  const source = readFileSync(new URL(path, import.meta.url), 'utf8');
  const code = ts.transpileModule(source, { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX,
  } }).outputText;
  const exports = {};
  vm.runInNewContext(code, { exports, URL, require: (id) =>
    id.endsWith('.css') ? {} : mocks[id] ?? (id.endsWith('/ui/Skeleton') ? skeletonComponents : require(id)) });
  return exports;
}
const preview = load('../src/components/ui/animationPreview.ts');
const playback = load('../src/components/ui/RenderedAnimation.tsx', { './animationPreview': preview });
const mediaHelpers = load('../src/components/characters/characterMediaItems.ts');
const { CharacterMediaProvider, CharacterMediaGallery, useCharacterMedia } = load('../src/components/characters/CharacterMedia.tsx', {
  '@/api/client': { apiUrl: (url) => url },
  '@/components/ui/Button': { Button: ({ children, icon, ...props }) => React.createElement('button', props, icon, children) },
  '@/components/ui/MediaViewer': { MediaViewer: () => null },
  '@/components/ui/MediaViewerProvider': { useMediaViewer: () => ({ open() {} }) },
  '@/components/ui/animationPreview': preview,
  './characterMediaItems': mediaHelpers,
});
const { CharacterArchive } = load('../src/components/characters/CharacterArchive.tsx', {
  '@/components/dialogue/PlayerText': { PlayerText: ({ value, fallback = '' }) => String(value || fallback).replace(/<[^>]*>/g, '') },
    '@/data/gameText': { gameTextPlainText: value => value.replace(/<[^>]*>/g, '') },
  '@/lib/router': { Link: 'a' },
  '../../api/client': { apiUrl: (url) => url },
  '../../data/entities': {},
  '../../data/locales': { availableLocales: [{ code: 'en', label: 'English' }] },
  '@/components/ui/Select': { Select: () => null },
  '@/components/ui/TrackPlayer': { TrackPlayer: () => null },
  '@/components/ui/RenderedAnimation': playback,
  '@/components/characters/CharacterMedia': { CharacterMediaGallery, useCharacterMedia },
  '@/components/ui/animationPreview': preview,
});
const clip = { animation: 'idle', url: 'https://cdn.test/idle.webm', mime_type: 'video/webm',
  file_id: 2, width: 786, height: 2048 };
const ready = { recipe: 'riglyph-native-v1', status: 'ready', variants: [clip] };
function render(animation_preview, hasArchive = true) {
  const archive = { profile: { voice_actors: {}, max_level: 90 }, skills: [], skill_tree: [], resonance_chain: [],
    voice_lines: [], other_voice_events: [],
    artwork: [{ kind: 'RoleHeadIconLarge', url: 'https://cdn.test/portrait.png' }],
    trial_spine: hasArchive ? { url: 'https://cdn.test/source.zip', file_id: 1, animation_preview } : null };
  return renderToStaticMarkup(React.createElement(CharacterMediaProvider, { archive, name: 'Denia' },
    React.createElement(CharacterArchive, { locale: 'en', characterKey: 'character:1211', materials: [], archive })));

}

for (const value of [undefined, { status: 'static' }, { status: 'failed' },
  { ...ready, variants: [] }, { ...ready, variants: [{ ...clip, url: 'file:///private' }] }]) {
  test(`unplayable trial hides the entire block and preserves artwork: ${JSON.stringify(value)}`, () => {
    const markup = render(value);
    assert.doesNotMatch(markup, /Character trial animation|Download trial scene|playback unavailable/);
    assert.match(markup, /https:\/\/cdn.test\/portrait.png/);
  });
}

test('a character without an archived trial has no trial block', () => {
  assert.doesNotMatch(render(null, false), /Character trial animation|Download trial scene/);
  assert.doesNotMatch(render(null, false), /Max level 90/);
  assert.match(render(null, false), /Artwork and animation/);
});

test('a playable trial joins the shared gallery with a paused first-frame thumbnail and no raw scene download', () => {
  const markup = render(ready);
  assert.match(markup, /Trial animation/);
  assert.match(markup, /Artwork and animation/);
  assert.match(markup, /<video[^>]*src="https:\/\/cdn.test\/idle.webm#t=0.001"/);
  assert.doesNotMatch(markup, /autoplay|<video[^>]*controls/);
  assert.doesNotMatch(markup, /character-trial-animation|href="https:\/\/cdn.test\/source.zip"/);
});
