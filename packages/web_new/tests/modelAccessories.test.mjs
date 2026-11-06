import assert from 'node:assert/strict';
import test from 'node:test';
import { declaredAccessories } from '../src/components/models/engine/accessories.js';

const anchor = (name, visible = true) => ({ visible: true, userData: {
  accessory_name: name, default_visible: visible,
  attachment_manifest: JSON.stringify({ name, visible }),
} });
const scene = (nodes) => ({ traverse: (visit) => nodes.forEach(visit) });

test('only uniquely declared accessory anchors are toggleable and defaults are respected', () => {
  const hat = anchor('Hat', false), bag = anchor('Bag');
  const unmarkedMesh = { name: 'Hat mesh', visible: true, userData: {} };
  const malformed = { userData: { accessory_name: 'Fake', default_visible: true, attachment_manifest: '{}' } };
  const result = declaredAccessories(scene([hat, bag, unmarkedMesh, malformed]));
  assert.deepEqual(result.options, [{ name: 'Hat', visible: false }, { name: 'Bag', visible: true }]);
  assert.equal(hat.visible, false);
  assert.equal(unmarkedMesh.visible, true);
  assert.equal(result.nodes.has('Fake'), false);
});

test('quality changes preserve visibility and ambiguous names are not guessed', () => {
  const bag = anchor('Bag');
  const result = declaredAccessories(scene([bag, anchor('Duplicate'), anchor('Duplicate')]), { Bag: false });
  assert.deepEqual(result.options, [{ name: 'Bag', visible: false }]);
  assert.equal(bag.visible, false);
});
