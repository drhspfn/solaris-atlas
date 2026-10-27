import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Bone, Group, Vector3 } from 'three';
import { SpringBones } from './spring-bones.js';

function rig() {
  const root = new Group(), bone = new Bone(), tip = new Bone();
  bone.name = 'chest'; tip.name = 'tip'; tip.position.x = 0.1;
  root.add(bone); bone.add(tip);
  const profile = { groups: [{ roots: ['chest'], excluded: [], dummyLength: 0.05,
    damping: 0.35, stiffness: 0.36, limitAngle: 15 }] };
  return { root, bone, tip, physics: new SpringBones(root, profile), profile };
}

test('inertia responds to movement, respects angular limits and restores the animated pose', () => {
  const { root, bone, physics } = rig();
  physics.update(1 / 60);
  physics.restore(); root.position.y = 0.025;
  physics.update(1 / 60);
  assert.ok(bone.quaternion.angleTo(physics.joints[0].base) > 0.001);
  assert.ok(bone.quaternion.angleTo(physics.joints[0].base) <= Math.PI / 12 + 1e-6);
  physics.restore();
  assert.ok(bone.quaternion.angleTo(physics.joints[0].base) < 1e-8);
  physics.reset(); physics.update(1 / 60);
  assert.ok(bone.quaternion.angleTo(physics.joints[0].base) < 1e-8);
});

test('long pauses and teleports reset inertia; a stationary rig settles without drift', () => {
  const { root, bone, physics } = rig();
  physics.update(1 / 60);
  root.position.y = 10;
  physics.restore(); physics.update(0.8);
  for (let i = 0; i < 300; i++) { physics.restore(); physics.update(1 / 60); }
  assert.ok(bone.quaternion.angleTo(physics.joints[0].base) < 1e-8);
  assert.ok(physics.joints[0].velocity.length() < 1e-8);
});

test('zero strength preserves animation and spring results are bounded at several frame rates', () => {
  for (const fps of [30, 60, 144]) {
    const { root, bone, physics } = rig();
    physics.update(1 / fps);
    for (let i = 0; i < fps * 3; i++) {
      physics.restore(); root.position.y = Math.sin(i / fps * 8) * 0.025;
      physics.update(1 / fps, 0);
      assert.ok(bone.quaternion.angleTo(physics.joints[0].base) < 1e-7);
      assert.ok(physics.joints[0].tail.toArray().every(Number.isFinite));
    }
  }
});

test('nested source roots are owned once; excluded subtrees are not simulated', () => {
  const { root, tip, profile } = rig();
  profile.groups.unshift({ ...profile.groups[0], roots: ['tip', 'missing'] });
  let physics = new SpringBones(root, profile);
  assert.equal(physics.joints.filter(j => j.bone === tip).length, 1);
  assert.deepEqual(physics.missing, ['missing']);
  profile.groups[1].excluded = ['tip']; profile.groups.shift();
  physics = new SpringBones(root, profile);
  assert.equal(physics.joints.length, 1);
  assert.ok(new Vector3().copy(physics.joints[0].offset).length() > 0);
});

test('physics stays relative to changing animation rotations and does not accumulate offsets', () => {
  const { root, bone, physics } = rig();
  for (let i = 0; i < 360; i++) {
    physics.restore();
    bone.rotation.z = Math.sin(i / 60 * 3) * 0.4;
    const animated = bone.quaternion.clone();
    root.position.y = Math.sin(i / 60 * 7) * 0.02;
    physics.update(1 / 60);
    assert.ok(bone.quaternion.angleTo(animated) <= Math.PI / 12 + 1e-6);
    physics.restore();
    assert.ok(bone.quaternion.angleTo(animated) < 1e-7);
  }
});
