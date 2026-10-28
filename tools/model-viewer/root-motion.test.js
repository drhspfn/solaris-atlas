import { test } from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import { RootMotionLock } from './root-motion.js';
import { BoneCamera } from './camera-rig.js';
function fixture() {
  const scene = new THREE.Scene(), stage = new THREE.Group(), model = new THREE.Group();
  const root = new THREE.Bone(), child = new THREE.Bone();
  root.name = 'Root'; child.name = 'Head'; child.position.y = 1;
  scene.add(stage); stage.add(model); model.add(root); root.add(child);
  const mesh = new THREE.SkinnedMesh(new THREE.BufferGeometry(), new THREE.MeshBasicMaterial());
  model.add(mesh); mesh.bind(new THREE.Skeleton([root, child]));
  return { stage, model, root, child, lock: new RootMotionLock(stage, model), mixer: new THREE.AnimationMixer(model) };
}
const world = object => object.getWorldPosition(new THREE.Vector3());
function close(actual, expected) {
  assert(actual.distanceTo(new THREE.Vector3(...expected)) < 1e-7, `${actual.toArray()} != ${expected}`);
}
test('planar lock preserves height, pose and rotation; toggling restores original travel without drift', () => {
  const { root, child, stage, lock } = fixture();
  root.position.set(3, 2, -4); root.rotation.y = 0.6; child.position.x = 0.3;
  lock.apply(); close(world(root), [3, 2, -4]);
  lock.enabled = true;
  for (let i = 0; i < 100; i++) lock.apply();
  close(world(root), [0, 2, 0]); close(stage.position, [-3, 0, 4]);
  close(root.position, [3, 2, -4]); close(child.position, [0.3, 1, 0]);
  assert.equal(root.rotation.y, 0.6);
  lock.enabled = false; lock.apply(); close(world(root), [3, 2, -4]); close(stage.position, [0, 0, 0]);
});
test('each world axis can be locked independently, including under rotated/scaled armatures', () => {
  const { root, model, lock } = fixture(); lock.enabled = true;
  for (const axis of ['x', 'y', 'z']) {
    root.position.set(3, 2, -4); lock.axes = { x: false, y: false, z: false, [axis]: true };
    lock.apply(); const expected = [3, 2, -4]; expected['xyz'.indexOf(axis)] = 0;
    close(world(root), expected);
  }
  model.rotation.x = -Math.PI / 2; model.scale.setScalar(0.01);
  root.position.set(300, 400, 200); lock.axes = { x: true, y: false, z: true }; lock.apply();
  close(world(root), [0, 2, 0]);
});
test('mixer playback, seeks, loops and clip changes keep authored tracks intact', () => {
  const { root, mixer, lock } = fixture();
  const clip = new THREE.AnimationClip('Travel', 2, [new THREE.VectorKeyframeTrack(root.uuid + '.position', [0, 1, 2], [0, 0, 0, 4, 2, 6, 8, 0, 12])]);
  const original = [...clip.tracks[0].values];
  let action = mixer.clipAction(clip).play(); lock.enabled = true;
  lock.restore(); mixer.update(0.5); lock.apply(); close(world(root), [0, 1, 0]);
  action.time = 1; mixer.update(0); lock.apply(); close(world(root), [0, 2, 0]);
  lock.restore(); mixer.update(1.5); lock.apply(); close(world(root), [0, 1, 0]);
  mixer.stopAllAction();
  mixer.clipAction(new THREE.AnimationClip('Other', 1, [new THREE.VectorKeyframeTrack(root.uuid + '.position', [0, 1], [20, 3, -20, 30, 5, -30])])).play();
  mixer.update(0); lock.apply(); close(world(root), [0, 3, 0]);
  lock.enabled = false; lock.apply(); close(world(root), [20, 3, -20]);
  assert.deepEqual([...clip.tracks[0].values], original);
});
test('bone-follow camera sees corrected travel and still follows vertical movement', () => {
  const { root, child, lock } = fixture();
  const camera = new THREE.PerspectiveCamera(); camera.position.set(0, 1, 3);
  const controls = { target: new THREE.Vector3(0, 1, 0), update() {} };
  const follow = new BoneCamera(camera, controls); follow.attach(child);
  lock.enabled = true; root.position.set(10, 2, 20); lock.apply(); follow.update();
  close(controls.target, [0, 3, 0]); close(camera.position, [0, 3, 3]);
});
test('shared meshes deduplicate their rig; unrelated skeletons and static models are not guessed', () => {
  const { stage, model, root, child } = fixture();
  const mesh = new THREE.SkinnedMesh(new THREE.BufferGeometry(), new THREE.MeshBasicMaterial());
  model.add(mesh); mesh.bind(new THREE.Skeleton([root, child]));
  assert.equal(new RootMotionLock(stage, model).root, root);
  const other = new THREE.Bone(); model.add(other); mesh.bind(new THREE.Skeleton([other]));
  const lock = new RootMotionLock(stage, model); assert.equal(lock.root, null);
  lock.enabled = true; assert.doesNotThrow(() => lock.apply());
  assert.equal(new RootMotionLock(new THREE.Group(), new THREE.Group()).root, null);
});
