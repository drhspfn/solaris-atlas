import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import { localizedText } from '../src/data/localized.ts';

const exports = {};
vm.runInNewContext(ts.transpileModule(readFileSync(new URL('../src/components/dialogue/speakerName.ts', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText, { exports, require: () => ({ localizedText }) });
const { dialogueSpeakerName } = exports;

test('an authored anonymous line does not gain an invented narrator name', () => {
  assert.equal(dialogueSpeakerName({ canonical_key: 'speaker:83', label: '', name_hidden: true }), null);
});

test('unknown and named speakers remain distinct from narration', () => {
  assert.equal(dialogueSpeakerName({ label: 'speaker:999' }), 'Unknown speaker');
  assert.equal(dialogueSpeakerName({ label: '???' }), '???');
  assert.equal(dialogueSpeakerName({ label: 'Cristoforo' }), 'Cristoforo');
});
