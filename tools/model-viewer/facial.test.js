import test from "node:test";
import assert from "node:assert/strict";
import { FacialAnimation } from "./facial.js";

const pack = () => ({ format: "solaris-facial-v1", clips: [
  { name: "Attack", body_clip: "Attack", seconds: 1, tracks: [
    { target: "E_Close", keys: [[0, 0], [0.5, 1], [1, 0]] },
  ] },
  { name: "Smile", seconds: 0, tracks: [{ target: "E_Close", keys: [[0, 0.25]] }] },
] });
const mesh = () => ({ morphTargetDictionary: { e_close: 0 }, morphTargetInfluences: [0.1] });
const model = (...meshes) => ({ traverse(fn) { meshes.forEach(fn); } });

test("face follows body time, seeks, resets stale weights and applies to every primitive", () => {
  const a = mesh(), b = mesh(), face = new FacialAnimation(pack(), model(a, b));
  face.update("Attack", 0.25);
  assert.equal(a.morphTargetInfluences[0], 0.5);
  assert.equal(b.morphTargetInfluences[0], 0.5);
  face.update("Attack", 0.75);
  assert.equal(a.morphTargetInfluences[0], 0.5);
  face.update("Idle", 0);
  assert.equal(a.morphTargetInfluences[0], 0.1);
  face.select("Smile"); face.update("Idle", 0);
  assert.equal(a.morphTargetInfluences[0], 0.25);
  face.select("off"); face.update("Attack", 0.5);
  assert.equal(a.morphTargetInfluences[0], 0.1);
});

test("independent facial animation loops and stops advancing while paused", () => {
  const a = mesh(), face = new FacialAnimation(pack(), model(a));
  face.select("Attack"); face.update("Idle", 0, 0.5);
  assert.equal(a.morphTargetInfluences[0], 1);
  face.update("Idle", 0, 0);
  assert.equal(a.morphTargetInfluences[0], 1);
  face.update("Idle", 0, 0.75);
  assert.equal(a.morphTargetInfluences[0], 0.5);
});

test("face refuses missing morphs, ambiguous channels and malformed samples", () => {
  assert.throws(() => new FacialAnimation(pack(), model({})), /full model/);
  for (const keys of [[[0, NaN]], [[-1, 0]], [[1, 0], [0, 1]]]) {
    const data = pack(); data.clips[0].tracks[0].keys = keys;
    assert.throws(() => new FacialAnimation(data, model(mesh())), /samples/);
  }
  const data = pack(); data.clips[1].body_clip = "Attack";
  assert.throws(() => new FacialAnimation(data, model(mesh())), /Ambiguous/);
});
