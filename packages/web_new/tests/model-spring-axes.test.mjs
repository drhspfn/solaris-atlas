import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Bone, Group, Vector3 } from 'three';
import { SpringBones } from '../src/components/models/engine/spring-bones.js';

function create(axis, withChild = false) {
  const root = new Group(), bone = new Bone();
  bone.name = 'spring'; root.add(bone);
  if (withChild) {
    const child = new Bone(); child.name = 'child'; child.position.set(0.03, 0.04, 0);
    bone.add(child);
  }
  const group = { roots: ['spring'], excluded: [], dummyLength: 0.05,
    damping: 0.2, stiffness: 0.65, limitAngle: 20 };
  if (axis !== undefined) group.dummyAxis = axis;
  return { root, bone, physics: new SpringBones(root, { groups: [group] }) };
}

test('all authored directions drive terminal dummy bones in glTF joint space', () => {
  for (const axis of [[1, 0, 0], [-1, 0, 0], [0, 0, 1], [0, 0, -1], [0, 1, 0], [0, -1, 0]]) {
    const { root, bone, physics } = create(axis);
    assert.ok(physics.joints[0].offset.distanceTo(new Vector3(...axis).multiplyScalar(0.05)) < 1e-10);
    bone.rotation.y = 0.4;
    physics.update(1 / 60);
    const expected = physics.joints[0].offset.clone().applyMatrix4(bone.matrixWorld);
    assert.ok(physics.joints[0].tail.distanceTo(expected) < 1e-8);
    physics.restore(); root.position.x += axis[0] ? 0 : 0.01; root.position.y += axis[0] ? 0.01 : 0;
    physics.update(1 / 60);
    assert.ok(bone.quaternion.angleTo(physics.joints[0].base) > 1e-5);
  }
});

test('legacy profiles retain positive X and child bones retain their actual offsets', () => {
  assert.deepEqual(create(undefined).physics.joints[0].offset.toArray(), [0.05, 0, 0]);
  assert.deepEqual(create([0, 0, -1], true).physics.joints[0].offset.toArray(), [0.03, 0.04, 0]);
});

test('malformed axes are rejected', () => {
  for (const axis of [null, [], [1, 1, 0], [0, 0, 0], [0, 0, 2], ['1', 0, 0], [1, , 0], 'X']) {
    assert.throws(() => create(axis), /axis/);
  }
});
