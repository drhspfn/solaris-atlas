import assert from 'node:assert/strict';
import { after, before, test } from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer } from 'vite';
import { cutsceneEvent } from './fixtures/cutsceneDescription.mjs';

let vite, Cutscene, AuthProvider, NarrativePreferencesProvider;
before(async () => {
  vite = await createServer({ server: { middlewareMode: true, hmr: false }, appType: 'custom' });
  ({ Cutscene } = await vite.ssrLoadModule('/src/components/story/QuestCutscenes.tsx'));
  ({ AuthProvider } = await vite.ssrLoadModule('/src/auth/AuthProvider.tsx'));
  ({ NarrativePreferencesProvider } = await vite.ssrLoadModule(
    '/src/preferences/NarrativePreferences.tsx',
  ));
});
after(async () => {
  await vite?.close();
});
const render = (event) =>
  renderToStaticMarkup(
    createElement(
      AuthProvider,
      null,
      createElement(NarrativePreferencesProvider, null, createElement(Cutscene, { event })),
    ),
  );

test('visual description is a closed AI disclosure with timed chapter controls', () => {
  const html = render(cutsceneEvent);
  assert.match(html, /<details class="cutscene-description"><summary>/);
  assert.ok(html.includes('AI description'));
  assert.ok(html.includes('0:30 · The shoreline'));
  assert.ok(html.includes('sampled visual observations'));
  assert.ok(!html.includes('autoplay'));
});

test('missing visual evidence has no empty description and keeps the player', () => {
  const event = structuredClone(cutsceneEvent);
  delete event.playback.media.scene.description;
  const html = render(event);
  assert.ok(!html.includes('cutscene-description'));
  assert.ok(html.includes('<video'));
});
