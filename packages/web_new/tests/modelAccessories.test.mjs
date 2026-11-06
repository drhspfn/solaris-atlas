import assert from 'node:assert/strict';
import test from 'node:test';
import * as THREE from 'three';
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

test('bone visibility masks animated scale and restores the authored scale', () => {
  const root = new THREE.Group(), bone = new THREE.Bone();
  bone.name = 'Bone_Piao027_M';
  bone.scale.set(2, 3, 4);
  bone.userData = { accessory_name: `Bone: ${bone.name}`, default_visible: true,
    attachment_manifest: JSON.stringify({ name: `Bone: ${bone.name}`, visible: true, kind: 'bone_visibility', bone: bone.name,
      events: [{ sequence: 'Test', time: 0, bone: bone.name, hide: true, chance: 1, source: 'notify' }] }) };
  root.add(bone);
  const accessories = declaredAccessories(root);
  const name = `Bone: ${bone.name}`;
  assert.equal(accessories.options[0].visible, true, 'notify events do not determine the initial state');
  const mixer = new THREE.AnimationMixer(root);
  const clip = new THREE.AnimationClip('scale', 1, [new THREE.VectorKeyframeTrack(`${bone.name}.scale`, [0, 1], [1, 1, 1, 3, 3, 3])]);
  mixer.clipAction(clip).play();
  accessories.set(name, false);
  assert.equal(bone.visible, true);
  assert.deepEqual(bone.scale.toArray(), [0, 0, 0]);
  accessories.restore(); mixer.update(0.5);
  assert.deepEqual(bone.scale.toArray(), [2, 2, 2], 'animation attempts to overwrite the mask');
  accessories.apply();
  assert.deepEqual(bone.scale.toArray(), [0, 0, 0]);
  accessories.set(name, true);
  assert.deepEqual(bone.scale.toArray(), [2, 3, 4]);
});
