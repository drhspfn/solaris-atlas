import test from 'node:test';
import assert from 'node:assert/strict';
import { requestVoicePlayback, startVoicePlayback } from '../src/components/ui/voicePlayback.ts';

test('voice playback pauses earlier tracks only after a newer track starts', () => {
  const audio = () => ({ paused: true, currentTime: 12, pause() { this.paused = true; } });
  const first = audio();
  const second = audio();
  const third = audio();
  globalThis.document = { querySelectorAll: () => [first, second, third] };
  requestVoicePlayback(first);
  first.paused = false;
  startVoicePlayback(first);
  requestVoicePlayback(second);
  assert.equal(first.paused, false);
  requestVoicePlayback(third);
  third.paused = false;
  startVoicePlayback(third);
  assert.equal(first.paused, true);
  assert.equal(first.currentTime, 12);
  second.paused = false;
  startVoicePlayback(second);
  assert.equal(second.paused, true);
  assert.equal(third.paused, false);
  delete globalThis.document;
});

import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';
import ts from 'typescript';
const require = createRequire(import.meta.url);
test('shared AudioPlayer media events coordinate playback without an extra layout wrapper', () => {
  const source = readFileSync(new URL('../src/components/ui/AudioPlayer.tsx', import.meta.url), 'utf8');
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  const exports = {}, calls = [];
  vm.runInNewContext(code, { exports, require: id => id === 'react'
    ? { useRef: () => ({ current: null }), useState: value => [value, () => {}], useEffect() {} }
    : id === './voicePlayback' ? { requestVoicePlayback: audio => calls.push(['request', audio]), startVoicePlayback: audio => calls.push(['start', audio]) }
    : id.endsWith('.css') ? {} : require(id) });
  const tree = exports.AudioPlayer({ src: '/voice.ogg' });
  const nodes = node => !node || typeof node !== 'object' ? [] : [node, ...[node.props?.children].flat(Infinity).flatMap(nodes)];
  const nativeAudio = nodes(tree).find(node => node.type === 'audio');
  const element = {};
  nativeAudio.props.onPlay({ currentTarget: element });
  nativeAudio.props.onPlaying({ currentTarget: element });
  assert.equal(tree.props.className, 'audio-player');
  assert.deepEqual(calls, [['request', element], ['start', element]]);
});
