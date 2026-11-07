import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const require = createRequire(import.meta.url);
async function metadata(data, archive = null) {
  const code = ts.transpileModule(readFileSync(new URL('../src/app/characters/[key]/page.tsx', import.meta.url), 'utf8'),
    { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  const mocks = {
    '@/api/serverApi': { serverApi: async (path) => path.includes('/profile?') ? data : archive },
    '@/lib/routeKey': { decodeRouteKey: decodeURIComponent, publicPath: () => '/characters/canonical' },
    '@/lib/canonicalRoute': { canonicalRoute: async () => {} },
    '@/components/profile/EntityProfileClient': { EntityProfileClient: 'Profile' },
    '@/data/characterArtwork': { characterPortrait: () => null },
    '@/data/localized': { localizedText: (value) => typeof value === 'string' ? value : value?.content || '' },
  };
  const exports = {};
  vm.runInNewContext(code, { exports, require: (id) => mocks[id] ?? require(id), process: { env: {} } });
  return exports.generateMetadata({ params: Promise.resolve({ key: 'abby' }) });
}
function assertTitles(result, title) {
  assert.equal(result.title, title);
  assert.equal(result.openGraph.title, `${title} | Solaris Atlas`);
  assert.equal(result.twitter.title, `${title} | Solaris Atlas`);
  assert.equal(result.alternates.canonical, '/characters/canonical');
  assert.equal(result.openGraph.description, result.description);
  assert.equal(result.twitter.description, result.description);
}
test('NPC metadata uses NPC data even when an archive biography exists', async () => {
  const result = await metadata({ character: { name: 'Abby', playable: false }, npc: { name_scope: 'blueprint' } }, { biography: 'Unrelated biography' });
  assertTitles(result, 'Abby — NPC Profile');
  assert.match(result.description, /locations, interactions and story appearances/);
  assert.equal(result.twitter.card, 'summary');
});
test('mixed NPC template preserves its source-provided description', async () => {
  const description = 'This game configuration is shared by differently named world actors.';
  const result = await metadata({ character: { name: 'Shared NPC template', description }, npc: { name_scope: 'blueprint' } });
  assertTitles(result, 'Shared NPC template — NPC Profile');
  assert.equal(result.description, description);
});
test('playable and other story roles use their type and keep biographies', async () => {
  const playable = await metadata({ character: { name: 'Camellya', playable: true } }, { biography: '<b>Resonator</b>  biography' });
  assertTitles(playable, 'Camellya — Resonator Profile');
  assert.equal(playable.description, 'Resonator biography');
  const story = await metadata({ character: { name: 'Story role', playable: false } });
  assertTitles(story, 'Story role — Character Profile');
});
test('missing profile uses a neutral character fallback', async () => {
  const result = await metadata(null);
  assertTitles(result, 'Character · abby — Character Profile');
});
