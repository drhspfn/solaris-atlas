import { test } from 'node:test';
import assert from 'node:assert/strict';
import { physicsProfile } from './stage.mjs';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

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

test('assets-only staging includes the lazy-animation runtime without game files', async () => {
  const output = await mkdtemp(join(tmpdir(), 'model-viewer-stage-'));
  try {
    execFileSync(process.execPath, [fileURLToPath(new URL('./stage.mjs', import.meta.url)),
      '--assets-only', '--output', output]);
    assert.equal(await readFile(join(output, 'animations.js'), 'utf8'),
      await readFile(new URL('./animations.js', import.meta.url), 'utf8'));
    assert.match(await readFile(join(output, 'viewer.js'), 'utf8'), /from ['"]\.\/animations.js['"]/);
    assert.equal(await readFile(join(output, 'root-motion.js'), 'utf8'),
      await readFile(new URL('./root-motion.js', import.meta.url), 'utf8'));
    for (const font of ['DMSans', 'Manrope']) {
      assert.equal((await readFile(join(output, 'fonts', `${font}.ttf`))).readUInt32BE(0), 0x00010000);
      assert.match(await readFile(join(output, 'fonts', `${font}-OFL.txt`), 'utf8'), /SIL OPEN FONT LICENSE/);
    }
  } finally {
    await rm(output, { recursive: true, force: true });
  }
});
