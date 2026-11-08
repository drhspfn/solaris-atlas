import assert from 'node:assert/strict';
import test from 'node:test';
import { animationPreviewUrl, selectAnimationClip } from '../src/components/ui/animationPreview.ts';

const clip = (animation, extra = {}) => ({
  animation, url: `https://cdn.test/${animation}.webm`, file_id: 12,
  mime_type: 'video/webm', loop: false, duration_seconds: 4, ...extra,
});
const preview = (...variants) => ({ recipe: 'riglyph-native-v1', status: 'ready', variants });

test('story uses the exact authored cue rather than the first available clip', () => {
  const pose = clip('1', { duration_seconds: 0 });
  const transition = clip('1to2');
  const media = preview(clip('idle'), pose, transition);
  assert.equal(selectAnimationClip(media, '1to2'), transition);
  assert.equal(selectAnimationClip(media, '1'), pose);
  for (const missing of [null, undefined, '', 'start', '2to3'])
    assert.equal(selectAnimationClip(media, missing), null);
});

test('trial idle is selected explicitly; no other available animation substitutes for it', () => {
  const idle = clip('idle', { loop: true });
  assert.equal(selectAnimationClip(preview(clip('enter'), idle), 'idle'), idle);
  assert.equal(selectAnimationClip(preview(clip('enter')), 'idle'), null);
});

test('a published default applies only without an explicit authored cue', () => {
  const idle = clip('idle');
  const transition = clip('1to2');
  const media = { ...preview(transition, idle), default_animation: 'idle' };
  assert.equal(selectAnimationClip(media, null), idle);
  assert.equal(selectAnimationClip(media, '1to2'), transition);
  assert.equal(selectAnimationClip(media, 'unknown'), null);
  assert.equal(selectAnimationClip({ ...media, default_animation: 'missing' }, null), null);
});

test('Rover clips match the chosen identity and never default to the other Rover', () => {
  const male = clip('1', { rover: 'male' });
  const female = clip('1', { rover: 'female' });
  const neutral = clip('1');
  assert.equal(selectAnimationClip(preview(male, female), '1'), null);
  assert.equal(selectAnimationClip(preview(male, female), '1', 'female'), female);
  assert.equal(selectAnimationClip(preview(male), '1', 'female'), null);
  assert.equal(selectAnimationClip(preview(neutral, female), '1', 'female'), female);
  assert.equal(selectAnimationClip(preview(male, neutral), '1', 'female'), neutral);
});

test('unpublished or unsupported preview recipes keep static fallback', () => {
  assert.equal(selectAnimationClip(null, 'idle'), null);
  assert.equal(selectAnimationClip({ ...preview(clip('idle')), status: 'failed' }, 'idle'), null);
  assert.equal(selectAnimationClip({ ...preview(clip('idle')), recipe: 'other' }, 'idle'), null);
});

test('only HTTP WebM previews are playable; archive URLs are never treated as videos', () => {
  for (const url of ['javascript:alert(1)', 'file:///secret', '/relative.webm', 'not a URL'])
    assert.equal(animationPreviewUrl(clip('idle', { url })), null);
  assert.equal(animationPreviewUrl(clip('idle', { mime_type: 'application/zip' })), null);
  assert.equal(animationPreviewUrl(clip('idle')), 'https://cdn.test/idle.webm');
  assert.equal(selectAnimationClip(preview(clip('idle', { url: '/bad' })), 'idle'), null);
});


test('version-two published previews select the same exact authored clip', () => {
  const idle = clip('idle');
  assert.equal(selectAnimationClip({ ...preview(idle), recipe: 'riglyph-native-v2' }, 'idle'), idle);
  assert.equal(selectAnimationClip({ ...preview(idle), recipe: 'riglyph-native-v2' }, 'missing'), null);
});
