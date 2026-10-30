import assert from 'node:assert/strict';
import test from 'node:test';
import { evaluateScene, sceneDuration } from '../src/components/story/openScenePose.ts';

// Hand-built neutral fixtures; no runtime, exporter, renderer, or asset dependencies.
function fixture(timelines = []) {
  return {
    format: 'solaris.skeletal-scene.v1',
    version: '4.1.23',
    bounds: [0, 0, 1, 1],
    bones: [
      { name: 'root', parent: null, transform: [0, 0, 0, 1, 1, 0, 0, 0], mode: 0, skin: false },
    ],
    slots: [
      { name: 'face', bone: 0, color: 'ffffffff', dark: 'ffffffff', attachment: 'mesh', blend: 0 },
    ],
    skins: [
      {
        name: 'default',
        slots: {
          0: {
            mesh: {
              name: 'mesh',
              type: 2,
              path: 'face.png',
              vertices: [1, 0, 0, 1, 0, 0],
              uvs: [1, 0, 0, 1, 0, 0],
              triangles: [0, 1, 2],
              hull: 3,
              color: 'ffffffff',
            },
          },
        },
      },
    ],
    animations: [{ name: 'idle', timelines }],
  };
}
const bone = (type, frames, target = 0) => ({ kind: 'bone', target, type, frames });
const key = (time, values, curve = 0) => ({ time, values, curve });
const mesh = (scene) => scene.skins[0].slots[0].mesh;
const pose = (scene, time, options) => evaluateScene(scene, 'idle', time, options);
function near(actual, expected) {
  assert.equal(actual.length, expected.length);
  actual.forEach((v, i) =>
    assert.ok(Math.abs(v - expected[i]) < 1e-9, `${i}: ${v} != ${expected[i]}`),
  );
}

test('translation changes positions from setup; calls leave input and earlier results untouched', () => {
  const scene = fixture([bone(1, [key(0, [0, 0]), key(2, [8, 4])])]);
  scene.bones[0].transform[1] = 3;
  const before = structuredClone(scene);
  const first = pose(scene, 0);
  near(first.draws[0].vertices, [4, 0, 3, 1, 3, 0]);
  near(pose(scene, 1).draws[0].vertices, [8, 2, 7, 3, 7, 2]);
  assert.equal(sceneDuration(scene, 'idle'), 2);
  assert.equal(first.duration, 2);
  assert.deepEqual(scene, before);
  const draw = first.draws[0];
  assert.equal(draw.sourceSlot, scene.slots[0]);
  assert.equal(draw.sourceAttachment, mesh(scene));
  draw.vertices[0] = 100;
  draw.uvs[0] = 100;
  draw.triangles[0] = 100;
  assert.deepEqual(scene, before);
  near(pose(scene, 0).draws[0].vertices, [4, 0, 3, 1, 3, 0]);
});

test('parent rotation, translation and scale compose with child local transform', () => {
  const scene = fixture([bone(0, [key(0, [0]), key(1, [90])])]);
  scene.bones[0].transform = [0, 10, 20, 2, 3, 0, 0, 0];
  scene.bones.push({
    name: 'child',
    parent: 0,
    transform: [0, 2, 0, 1, 1, 0, 0, 0],
    mode: 0,
    skin: false,
  });
  scene.slots[0].bone = 1;
  near(pose(scene, 1).draws[0].vertices, [10, 26, 7, 24, 10, 24]);
});

test('all ten bone timeline types use offsets except scales, which multiply setup', () => {
  const cases = [
    [0, [90], [0, 1, -1, 0, 0, 0]],
    [1, [2, 3], [3, 3, 2, 4, 2, 3]],
    [2, [2], [3, 0, 2, 1, 2, 0]],
    [3, [3], [1, 3, 0, 4, 0, 3]],
    [4, [2, 3], [4, 0, 0, 12, 0, 0]],
    [5, [2], [4, 0, 0, 4, 0, 0]],
    [6, [3], [2, 0, 0, 12, 0, 0]],
    [7, [90, -90], [0, 1, 1, 0, 0, 0]],
    [8, [90], [0, 1, 0, 1, 0, 0]],
    [9, [-90], [1, 0, 1, 0, 0, 0]],
  ];
  for (const [type, values, expected] of cases) {
    const scene = fixture([bone(type, [key(0, values)])]);
    if (type >= 4 && type <= 6) scene.bones[0].transform.splice(3, 2, 2, 4);
    near(pose(scene, 0).draws[0].vertices, expected);
  }
});

test('animated slot opacity and RGB multiply attachment tint; draw order and blends persist', () => {
  const scene = fixture([
    { kind: 'slot', type: 1, target: 0, frames: [key(0, [1, 1, 1, 1]), key(1, [0, 0.5, 1, 0])] },
  ]);
  mesh(scene).color = '80402080';
  for (const blend of [1, 2, 3]) {
    scene.slots.push({ ...scene.slots[0], name: `slot${blend}`, blend });
    scene.skins[0].slots[blend] = { mesh: mesh(scene) };
  }
  const result = pose(scene, 0.5);
  near(result.draws[0].color, [(0.5 * 128) / 255, (0.75 * 64) / 255, 32 / 255, (0.5 * 128) / 255]);
  assert.deepEqual(
    result.draws.map((draw) => draw.slot),
    [0, 1, 2, 3],
  );
  assert.deepEqual(
    result.draws.map((draw) => draw.blend),
    [0, 1, 2, 3],
  );
});

test('stepped segments hold until next key and pre-first-key sampling uses setup', () => {
  const scene = fixture([bone(2, [key(1, [4], 1), key(2, [10])])]);
  near(pose(scene, 0).draws[0].vertices, [1, 0, 0, 1, 0, 0]);
  assert.equal(pose(scene, 1.99).draws[0].vertices[0], 5);
  assert.equal(pose(scene, 2).draws[0].vertices[0], 11);
});

test('Bezier controls are absolute time/value coordinates, independently per channel', () => {
  const scene = fixture([
    bone(1, [key(2, [10, 20], [2, 10, 2, 10, 2, 20, 2, 28]), key(10, [18, 28])]),
  ]);
  // t=.5 on cubic parameter gives time=3, X=11, Y=24.
  near(pose(scene, 3).draws[0].vertices, [12, 24, 11, 25, 11, 24]);
});

test('sparse unweighted deforms interpolate zero-filled offsets and reset every call', () => {
  const scene = fixture([
    {
      kind: 'deform',
      skin: 0,
      target: 0,
      name: 'mesh',
      frames: [
        { ...key(1, [4]), offset: 0 },
        { ...key(3, [8]), offset: 3 },
      ],
    },
  ]);
  near(pose(scene, 2).draws[0].vertices, [3, 0, 0, 5, 0, 0]);
  near(pose(scene, 3).draws[0].vertices, [1, 0, 0, 9, 0, 0]);
  near(pose(scene, 0).draws[0].vertices, [1, 0, 0, 1, 0, 0]);
  near(pose(scene, 2).draws[0].vertices, [3, 0, 0, 5, 0, 0]);
  assert.deepEqual(mesh(scene).vertices, [1, 0, 0, 1, 0, 0]);
});

test('monotone Bezier time curves permit crossed handles and reject time reversal', () => {
  const scene = fixture([bone(2, [key(0, [0], [1, 0, 0, 8]), key(1, [8])])]);
  assert.ok(Math.abs(pose(scene, 0.5).draws[0].vertices[0] - 5) < 1e-4);
  scene.animations[0].timelines[0].frames[0].curve = [2, 0, -1, 8];
  assert.throws(() => pose(scene, 0), /non-monotone Bezier/);
});

test('weighted deformation offsets each influence before world transform and weighting', () => {
  const scene = fixture([
    {
      kind: 'deform',
      skin: 0,
      target: 0,
      name: 'mesh',
      frames: [
        { ...key(0, []), offset: 0 },
        { ...key(1, [4, 2]), offset: 2 },
      ],
    },
  ]);
  scene.bones.push({
    name: 'second',
    parent: null,
    transform: [0, 10, 0, 2, 1, 0, 0, 0],
    mode: 0,
    skin: false,
  });
  mesh(scene).vertices = [
    [
      [0, 2, 0, 0.25],
      [1, 1, 0, 0.75],
    ],
  ];
  mesh(scene).uvs = [0, 0];
  mesh(scene).triangles = [];
  mesh(scene).hull = 1;
  near(pose(scene, 1).draws[0].vertices, [15.5, 1.5]);
  near(pose(scene, 0).draws[0].vertices, [9.5, 0]);
  near(pose(scene, 1).draws[0].vertices, [15.5, 1.5]);
});

test('deform uses scalar Bezier progress and stepped segments', () => {
  const scene = fixture([
    {
      kind: 'deform',
      skin: 0,
      target: 0,
      name: 'mesh',
      frames: [
        { ...key(2, [0], [2, 0, 2, 0]), offset: 0 },
        { ...key(10, [8]), offset: 0 },
      ],
    },
  ]);
  assert.ok(Math.abs(pose(scene, 3).draws[0].vertices[0] - 2) < 1e-9);
  scene.animations[0].timelines[0].frames[0].curve = 1;
  assert.equal(pose(scene, 9).draws[0].vertices[0], 1);
});

test('attachment keys switch and hide draws, retaining original binding objects', () => {
  const scene = fixture([
    {
      kind: 'slot',
      target: 0,
      type: 0,
      frames: [
        { time: 1, name: 'other' },
        { time: 2, name: null },
      ],
    },
  ]);
  const other = { ...mesh(scene), name: 'other', path: 'other.png' };
  scene.skins[0].slots[0].other = other;
  assert.equal(pose(scene, 0).draws[0].attachment, 'mesh');
  assert.equal(pose(scene, 1).draws[0].sourceAttachment, other);
  assert.deepEqual(pose(scene, 2).draws, []);
});

test('nonloop clamps; loop wraps; zero-duration and empty clips remain stable', () => {
  const scene = fixture([bone(2, [key(0, [0]), key(2, [10])])]);
  assert.equal(pose(scene, -5).draws[0].vertices[0], 1);
  assert.equal(pose(scene, -5, { loop: false }).time, 0);
  assert.equal(pose(scene, 20, { loop: false }).time, 2);
  assert.equal(pose(scene, 3, { loop: true }).time, 1);
  assert.equal(pose(scene, 20).draws[0].vertices[0], 11);
  assert.equal(pose(scene, 2, { loop: true }).draws[0].vertices[0], 1);
  assert.equal(pose(scene, 3, { loop: true }).draws[0].vertices[0], 6);
  assert.equal(pose(scene, -1, { loop: true }).draws[0].vertices[0], 6);
  for (const staticScene of [fixture(), fixture([bone(2, [key(0, [7])])])]) {
    assert.deepEqual(pose(staticScene, 0, { loop: true }), pose(staticScene, 500, { loop: true }));
    assert.equal(sceneDuration(staticScene, 'idle'), 0);
  }
});

test('invalid references and unsupported semantics fail explicitly', () => {
  const cases = [
    [
      (s) => {
        s.bones[0].mode = 1;
      },
      /unsupported bone/,
    ],
    [
      (s) => {
        s.bones[0].parent = 0;
      },
      /parent.*out of range/,
    ],
    [
      (s) => {
        s.slots[0].bone = 99;
      },
      /slot bone.*out of range/,
    ],
    [
      (s) => {
        s.slots[0].dark = '000000ff';
      },
      /dark tint/,
    ],
    [
      (s) => {
        s.slots[0].blend = 4;
      },
      /unsupported blend/,
    ],
    [
      (s) => {
        mesh(s).type = 1;
      },
      /unsupported attachment/,
    ],
    [
      (s) => {
        mesh(s).triangles[0] = 3;
      },
      /triangle index/,
    ],
    [
      (s) => {
        s.slots[0].attachment = 'missing';
      },
      /missing attachment/,
    ],
    [
      (s) => {
        s.animations[0].timelines = [bone(10, [])];
      },
      /timeline type.*out of range/,
    ],
    [
      (s) => {
        s.animations[0].timelines = [bone(0, [], 9)];
      },
      /bone timeline.*out of range/,
    ],
    [
      (s) => {
        s.animations[0].timelines = [{ kind: 'slot', target: 0, type: 2, frames: [] }];
      },
      /unsupported slot/,
    ],
    [
      (s) => {
        s.animations[0].timelines = [
          { kind: 'deform', skin: 1, target: 0, name: 'mesh', frames: [] },
        ];
      },
      /unsupported deform skin/,
    ],
    [
      (s) => {
        s.animations[0].timelines = [
          {
            kind: 'deform',
            skin: 0,
            target: 0,
            name: 'mesh',
            frames: [{ ...key(0, [1]), offset: 6 }],
          },
        ];
      },
      /deform offset/,
    ],
    [
      (s) => {
        s.animations[0].timelines = [bone(0, [key(1, [0]), key(0, [1])])];
      },
      /frame times/,
    ],
    [
      (s) => {
        s.animations[0].timelines = [bone(0, [key(0, [0], [0, 0]), key(1, [1])])];
      },
      /Bezier controls/,
    ],
    [
      (s) => {
        mesh(s).vertices = [[[99, 0, 0, 1]]];
      },
      /influence bone.*out of range/,
    ],
  ];
  for (const [mutate, error] of cases) {
    const scene = fixture();
    mutate(scene);
    assert.throws(() => pose(scene, 0), error);
  }
  assert.throws(() => pose(fixture(), NaN), /time must be finite/);
  assert.throws(() => sceneDuration(fixture(), 'missing'), /unknown animation/);
});
