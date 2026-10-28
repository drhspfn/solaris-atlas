import { test } from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import { BoneCamera, cameraBones } from './camera-rig.js';

function fixture() {
  const root = new THREE.Group(), bone = new THREE.Bone();
  bone.name = 'Bip001Head'; root.add(bone);
  const camera = new THREE.PerspectiveCamera(); camera.position.set(0, 1, 3);
  const controls = { target: new THREE.Vector3(0, 1, 0), update() {} };
  return { root, bone, camera, controls, rig: new BoneCamera(camera, controls) };
}
const close = (actual, expected) => assert(actual.distanceTo(new THREE.Vector3(...expected)) < 1e-8);

test('bone list contains actual joints only, prefers head and retains authored names', () => {
  const { root, bone } = fixture();
  const { options, preferred, bones } = cameraBones(root);
  assert.equal(options.length, 1); assert.equal(preferred, bone.uuid);
  assert.equal(options[0].label, 'Head · Bip001Head');
  assert.equal(bones.get(preferred), bone);
  assert(!options.some(option => option.label.includes('Nose')));
});

test('world following preserves angle, distance and manual orbit adjustments; detach stops tracking', () => {
  const { bone, camera, controls, rig } = fixture();
  bone.position.y = 2;
  assert(rig.attach(bone)); close(camera.position, [0, 2, 3]); close(controls.target, [0, 2, 0]);
  camera.position.x = 1; bone.position.set(2, 3, 0); rig.update();
  close(camera.position, [3, 3, 3]); close(controls.target, [2, 3, 0]);
  rig.detach(); bone.position.x = 9; rig.update(); close(camera.position, [3, 3, 3]);
});

test('local following rotates the orbit frame and respects target offsets', () => {
  const { bone, camera, controls, rig } = fixture();
  rig.attach(bone, { space: 'local', offset: new THREE.Vector3(0, 1, 0) });
  bone.rotation.y = Math.PI / 2; rig.update();
  close(camera.position, [3, 1, 0]); close(controls.target, [0, 1, 0]);
  rig.setOffset(0, 2, 0); close(camera.position, [3, 2, 0]);
  rig.setOffset(NaN, 0, 0); close(camera.position, [3, 2, 0]);
  rig.setSpace('world'); bone.rotation.y = Math.PI; rig.update(); close(camera.position, [3, 2, 0]);
});

test('framing does not start following and a missing bone cannot attach', () => {
  const { bone, camera, rig } = fixture();
  bone.position.y = 2; rig.frame(bone); assert.equal(rig.following, false);
  close(camera.position, [0, 2, 3]); assert.equal(rig.attach(null), false);
});
