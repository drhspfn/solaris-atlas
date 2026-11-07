import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
const require = createRequire(import.meta.url), cache = new Map();
function load(path) {
  if (cache.has(path.href)) return cache.get(path.href);
  const exports = {};
  vm.runInNewContext(ts.transpileModule(readFileSync(path, 'utf8'), { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX,
  } }).outputText, { exports, require: id => id.startsWith('.') ? load(new URL(`${id}.ts`, path)) : require(id) });
  cache.set(path.href, exports); return exports;
}
const { gameTextRuns, gameTextPlainText, genderText } = load(new URL('../src/data/gameText.ts', import.meta.url));
const { PlayerText } = load(new URL('../src/components/dialogue/PlayerText.tsx', import.meta.url));
const display = { name: 'Rover name', gender: 'female', colorMode: 'custom', customColor: '#112233' };
const render = (value, props = {}) => renderToStaticMarkup(createElement(PlayerText, { value, display, ...props }));

test('Japanese authored male and female alternatives follow the selected Rover', () => {
  const text = '{Male=もちろんだ。;Female=もちろんだよ。}';
  assert.equal(gameTextPlainText(text, display), 'もちろんだよ。');
  assert.equal(gameTextPlainText(text, { ...display, gender: 'male' }), 'もちろんだ。');
  assert.equal(gameTextPlainText(text, { ...display, gender: 'ask' }), 'もちろんだ。');
});
test('gender alternatives preserve nested player and markup tokens and literal semicolons', () => {
  const text = 'Hello {Female=<i>{PlayerName}</i>; yes;Male=Sir {PlayerName}}!';
  assert.equal(gameTextPlainText(text, display), 'Hello Rover name; yes!');
  assert.equal(genderText('{Male={Male=son;Female=daughter};Female=friend}', 'female'), 'friend');
  assert.equal(gameTextPlainText('{Male=he;Female=}', display), '');
});
test('unknown tokens and incomplete variants are preserved rather than misparsed', () => {
  assert.equal(genderText('{PlayerName} {QuestId} {Male=unfinished'), '{PlayerName} {QuestId} {Male=unfinished');
  assert.equal(genderText('{Male=only one}'), '{Male=only one}');
});
test('formatting, nested colors and annotation wrappers share one parser', () => {
  const runs = gameTextRuns('<color=Highlight><i>Golden</i> <b>Anthem</b></color><br/><u>last</u><s>old</s><ano=read>term</ano>');
  assert.equal(runs[0].italic, true); assert.match(runs[0].color, /^#/);
  assert.equal(runs[2].bold, true); assert.equal(runs[3].text, '\n');
  assert.equal(runs[4].underline, true); assert.equal(runs[5].strike, true);
  assert.equal(gameTextPlainText('<i>Golden Anthem</i>'), 'Golden Anthem');
});
test('the atomic renderer supports classes, styles and accessibility attributes', () => {
  const html = render('Hello {PlayerName}', { className: 'test-copy', style: { fontSize: 18 }, 'aria-label': 'Greeting' });
  assert.match(html, /class="test-copy"/); assert.match(html, /font-size:18px/);
  assert.match(html, /aria-label="Greeting"/); assert.match(html, /color:#112233/);
  assert.match(html, /Rover name/);
});
test('renderer uses the selected alternative and emphasis, with no raw game wrappers', () => {
  const html = render('{Male=His;Female=Her} <i>Golden Anthem</i>');
  assert.match(html, /Her/); assert.doesNotMatch(html, /His|Male=|Female=|&lt;i&gt;/);
  assert.match(html, /font-style:italic/);
});
test('unknown HTML and markup in player names stay escaped and never become executable', () => {
  const html = render('<img src=x onerror=alert(1)> {PlayerName}', { display: { ...display, name: '$&<script>bad</script>' } });
  assert.doesNotMatch(html, /<img|<script/); assert.match(html, /&lt;img/); assert.match(html, /\$&amp;&lt;script&gt;/);
  assert.equal(gameTextPlainText('{PlayerName}', { ...display, name: '$&' }), '$&');
});

 test('localized inline content follows the same formatter without hiding resolved text', () => {
 assert.equal(gameTextPlainText({ content: null, inline_text: '{Male=Sir;Female=Lady} {PlayerName}' }, display), 'Lady Rover name');
 assert.equal(gameTextPlainText({ content: 'Resolved', inline_text: 'Other' }, display), 'Resolved');
 });
