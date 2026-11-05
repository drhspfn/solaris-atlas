import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';
import test from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import ts from 'typescript';

const require = createRequire(import.meta.url);
const code = ts.transpileModule(readFileSync(new URL('../src/components/characters/CharacterArchive.tsx', import.meta.url), 'utf8'), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
const locales = [ ['en', 'English'], ['ja', '日本語'], ['ko', '한국어'], ['zh-Hans', '简体中文'] ]
  .map(([code, label]) => ({ code, label }));
const Select = () => null;
const TrackPlayer = () => null;
function setup() {
  let selected = '';
  const mocks = {
    react: { ...React, useState: () => [selected, value => { selected = value; }] },
    '@/lib/router': {}, '../../api/client': { apiUrl: url => url }, '../../data/entities': {},
    '../../data/locales': { availableLocales: locales }, '@/components/ui/Select': { Select },
    '@/components/ui/TrackPlayer': { TrackPlayer }, '@/components/characters/CharacterMedia': {},
  };
  const exports = {};
  vm.runInNewContext(code, { exports, require: id => id.endsWith('.css') ? {} : mocks[id] ?? require(id) });
  return (line, textLocale = 'en') => exports.CharacterVoicePlayback({ line, textLocale });
}
const variants = locales.flatMap(({ code }) => [2, 1].map(n => ({
  id: `child${n}`, source_name: `${code}:hirc1:child${n}`, language: code,
  url: `https://cdn.test/${code}/${n}.ogg`, status: 'available',
  selector_branches: [{ random: [{ mode: 'random', weight: 50000 }] }],
})));
const line = { id: 160843, title: 'Fulu', text: 'English text', audio_url: null, audio_tracks: {}, audio_variants: variants };
const children = tree => React.Children.toArray(tree.props.children);

test('eight authored alternatives require explicit choice and never infer gender or canonical audio', () => {
  const render = setup(), tree = render(line), [selector] = children(tree);
  assert.equal(selector.type, Select);
  assert.equal(selector.props.value, '');
  assert.equal(children(tree).some(child => child.type === TrackPlayer), false);
  assert.equal(selector.props.options.length, 8);
  for (const locale of locales) {
    const options = selector.props.options.filter(option => option.value.startsWith(`${locale.code}:`));
    assert.equal(options[0].label, `${locale.label} · Variant 1`);
    assert.equal(options[1].label, `${locale.label} · Variant 2`);
    assert.match(options[0].value, /child1$/);
  }
  assert.doesNotMatch(JSON.stringify(selector.props.options), /Male|Female|Rover|canonical|default/);
  assert.equal(line.text, 'English text');
});

test('selection delegates to generic player and survives text-language changes and reordered variants', () => {
  const render = setup();
  children(render(line))[0].props.onChange('ja:hirc1:child2');
  const tree = render({ ...line, audio_variants: [...variants].reverse() }, 'ko');
  const [selector, player] = children(tree);
  assert.equal(selector.props.value, 'ja:hirc1:child2');
  assert.equal(player.type, TrackPlayer);
  assert.equal(player.props.src, 'https://cdn.test/ja/2.ogg');
  assert.equal(player.props.label, 'Fulu · 日本語 · Variant 2');
  assert.equal(player.props.autoPlay, undefined);
  const changed = children(render({ ...line, audio_variants: variants.filter(v => v.source_name !== 'ja:hirc1:child2') }));
  assert.equal(changed[0].props.value, '');
  assert.equal(changed.some(child => child.type === TrackPlayer), false);
});

test('missing variants stay disabled, suppress ambiguous legacy fallback and retain other languages', () => {
  const render = setup();
  const missing = { ...line, audio_variants: [{ ...variants[0], url: null, status: 'missing' }],
    audio_url: 'https://cdn.test/guessed.ogg', audio_tracks: { en: 'https://cdn.test/ambiguous.ogg', ja: 'https://cdn.test/single.ogg' } };
  const tree = render(missing), [selector] = children(tree);
  assert.equal(selector.props.options.length, 2);
  assert.equal(selector.props.options[0].disabled, true);
  assert.match(selector.props.options[0].label, /unavailable/);
  selector.props.onChange(selector.props.options[0].value);
  assert.equal(children(render(missing)).some(child => child.type === TrackPlayer), false);
  selector.props.onChange('track:ja');
  assert.equal(children(render(missing))[1].props.src, 'https://cdn.test/single.ogg');
});

test('legacy single-track player keeps its multilingual contract', () => {
  const render = setup();
  const tree = render({ ...line, audio_variants: [], audio_url: '/api/voice/en', audio_tracks: { en: '/api/voice/en', ja: '/api/voice/ja' } });
  assert.equal(tree.type, TrackPlayer);
  assert.equal(tree.props.src, '/voice/en');
  assert.equal(tree.props.tracks.English, '/voice/en');
  assert.equal(tree.props.tracks['日本語'], '/voice/ja');
  assert.equal(tree.props.defaultTrack, 'English');
});

test('selected variant uses the unchanged generic player with no preload or autoplay', () => {
  function load(name, mocks) {
    const exports = {};
    const output = ts.transpileModule(readFileSync(new URL(`../src/components/ui/${name}.tsx`, import.meta.url), 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
    }).outputText;
    vm.runInNewContext(output, { exports, require: id => id.endsWith('.css') ? {} : mocks[id] ?? require(id) });
    return exports;
  }
  const audio = load('AudioPlayer', { './voicePlayback': {} });
  const generic = load('TrackPlayer', { './AudioPlayer': audio });
  const render = setup();
  children(render(line))[0].props.onChange('en:hirc1:child1');
  const player = children(render(line))[1];
  const html = renderToStaticMarkup(React.createElement(generic.TrackPlayer, player.props));
  assert.match(html, /<audio[^>]+src="https:\/\/cdn.test\/en\/1.ogg"[^>]+preload="none"/);
  assert.doesNotMatch(html, /autoplay|<audio[^>]*\bcontrols=/i);
});
