import assert from 'node:assert/strict';
import test from 'node:test';
import { selectCutsceneTracks } from '../src/components/story/cutsceneAudioSelection.ts';

const tracks = [
  { role: 'effects', language: null, url: 'common' },
  { role: 'voice', language: 'en', rover: 'male', url: 'male' },
  { role: 'voice', language: 'en', rover: 'female', url: 'female' },
  { role: 'voice', language: 'ja', rover: 'male', url: 'japanese' },
];
test('shared video plays only its selected Rover and language', () => {
  assert.deepEqual(selectCutsceneTracks(tracks, 'en', 'male').map(t => t.url), ['common', 'male']);
  assert.deepEqual(selectCutsceneTracks(tracks, 'en', 'female').map(t => t.url), ['common', 'female']);
});
test('Ask never mixes gender alternatives or silently chooses one', () => {
  assert.deepEqual(selectCutsceneTracks(tracks, 'en', 'ask').map(t => t.url), ['common']);
});
test('legacy tracks without selectors remain playable', () => {
  const legacy = [{ role: 'voice', language: 'en', url: 'legacy' }];
  assert.deepEqual(selectCutsceneTracks(legacy, 'en', 'ask'), legacy);
});
