import assert from 'node:assert/strict';
import test from 'node:test';
import { selectCharacterVariant } from '../src/data/characterIdentity.ts';

const variants = [
  { canonical_key: 'character:1501', gender: 'male', element_id: 5 },
  { canonical_key: 'character:1502', gender: 'female', element_id: 5 },
  { canonical_key: 'character:9911', gender: 'male', element_id: 9 },
  { canonical_key: 'character:9912', gender: 'female', element_id: 9 },
];

test('appearance changes preserve attribute and attribute changes preserve appearance', () => {
  const female = selectCharacterVariant(variants, variants[0], { gender: 'female' });
  assert.equal(female.canonical_key, 'character:1502');
  const futureElement = selectCharacterVariant(variants, female, { element_id: 9 });
  assert.equal(futureElement.canonical_key, 'character:9912');
  assert.equal(selectCharacterVariant(variants, futureElement, { gender: 'male' }).canonical_key, 'character:9911');
});

test('a missing variant never falls back to another gender or element', () => {
  assert.equal(selectCharacterVariant(variants, variants[0], { element_id: 100 }), undefined);
  assert.equal(selectCharacterVariant(variants.filter((row) => row.canonical_key !== 'character:9912'), variants[2], { gender: 'female' }), undefined);
});
