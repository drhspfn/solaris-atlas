import { test } from 'node:test';
import assert from 'node:assert/strict';
import { physicsProfile } from './stage.mjs';

const blueprint = () => [{ Name: 'Default__ABP', Properties: { Kawaii: {
  RootBones: [{ BoneName: 'Chest' }], ExcludeBones: [{ BoneName: 'Excluded' }],
  BoneForwardAxis: 'EBoneForwardAxis::X_Positive', DummyBoneLength: 5,
  PhysicsSettings: { Damping: 0.35, Stiffness: 0.36, LimitAngle: 15 },
} } }];

test('source groups, exclusions, limits and centimetres are retained with provenance', () => {
  const profile = physicsProfile(blueprint(), '/local/private/ABP.json', 'abc');
  assert.equal(profile.source, 'ABP.json');
  assert.equal(profile.sha256, 'abc');
  assert.deepEqual(profile.groups[0], { name: 'Kawaii', roots: ['Chest'], excluded: ['Excluded'],
    damping: 0.35, stiffness: 0.36, limitAngle: 15, dummyLength: 0.05 });
});

test('missing settings and unsupported coordinate axes fail before staging', () => {
  assert.throws(() => physicsProfile([], '', ''), /default/);
  const data = blueprint(); data[0].Properties.Kawaii.BoneForwardAxis = 'Y_Positive';
  assert.throws(() => physicsProfile(data, '', ''), /axis/);
  data[0].Properties.Kawaii.BoneForwardAxis = 'EBoneForwardAxis::X_Positive';
  data[0].Properties.Kawaii.PhysicsSettings.Damping = NaN;
  assert.throws(() => physicsProfile(data, '', ''), /Damping/);
});
