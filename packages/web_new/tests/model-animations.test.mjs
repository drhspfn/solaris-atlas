import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import * as THREE from "three";
import { BoneCamera, cameraBones } from "../src/components/models/engine/camera-rig.js";
import { RootMotionLock } from "../src/components/models/engine/root-motion.js";
import { FacialAnimation } from "../src/components/models/engine/facial.js";
import {
  resourceUrl,
  animationLoadLabel,
  bindAnimationClips,
  lazyAnimationPack,
  classifyAnimation,
  attachAnimationCatalog,
  prepareAnimatedMeshes,
} from "../src/components/models/engine/animations.js";

function rig({ suffix = "", duplicate = false, missing = false } = {}) {
  const scene = new THREE.Group();
  const bone = new THREE.Bone();
  bone.name = `Hips${suffix}`;
  scene.add(bone);
  const associations = new Map([[bone, { nodes: 0 }]]);
  if (duplicate) {
    const other = new THREE.Bone();
    scene.add(other);
    associations.set(other, { nodes: 0 });
  }
  return {
    scene,
    bone,
    parser: {
      associations,
      json: { nodes: [{ name: missing ? "Other" : "Hips" }] },
    },
    animations: [
      new THREE.AnimationClip("Stand1", 1, [
        new THREE.VectorKeyframeTrack(
          `${bone.name}.position`,
          [0, 1],
          [0, 0, 0, 2, 0, 0],
        ),
      ]),
    ],
  };
}

test("same-origin HTTP(S) validation and optional integer byte hint", () => {
  const base = "https://atlas.test/viewer/index.html";
  assert.equal(
    resourceUrl("../animations.glb", base),
    "https://atlas.test/animations.glb",
  );
  for (const value of [
    "https://elsewhere.test/a.glb",
    "//elsewhere.test/a",
    "javascript:alert(1)",
    "data:,bad",
    "blob:https://atlas.test/id",
    "http://atlas.test/a",
  ]) {
    assert.throws(() => resourceUrl(value, base), /viewer origin/);
  }
  assert.equal(animationLoadLabel("10485760"), "Load animations (~10.0 MiB)");
  for (const value of [
    null,
    "",
    "-1",
    "NaN",
    "1.5",
    "Infinity",
    "1e6",
    "9007199254740992",
  ]) {
    assert.equal(animationLoadLabel(value), "Load animations");
  }
});

test("resources stay on their viewer origin and reject credentials and downgrades", () => {
  for (const base of [
    "http://localhost:3000/viewer/",
    "https://atlas.test/viewer/",
  ]) {
    assert.throws(() => resourceUrl("https://cdn.solarisatlas.fun/a.glb", base), /origin/);
    assert.equal(
      resourceUrl("model.glb", base),
      new URL("model.glb", base).href,
    );
    for (const url of [
      "http://cdn.solarisatlas.fun/a",
      "https://cdn.solarisatlas.fun.evil.test/a",
      "https://other.solarisatlas.fun/a",
      "https://cdn.solarisatlas.fun:444/a",
      "https://cdn.solarisatlas.fun@evil.test/a",
      "https://user@cdn.solarisatlas.fun/a",
      "https://user:secret@cdn.solarisatlas.fun/a",
      new URL("https://user:secret@atlas.test/a").href,
      "data:,bad",
      "file:///model.glb",
    ]) {
      assert.throws(() => resourceUrl(url, base), /origin/);
    }
  }
  assert.equal(resourceUrl("/a.glb", "https://cdn.solarisatlas.fun/viewer/"), "https://cdn.solarisatlas.fun/a.glb");
  assert.throws(
    () =>
      resourceUrl(
        "http://localhost:3000/model.glb",
        "https://localhost:3000/viewer/",
      ),
    /origin/,
  );
  assert.throws(
    () =>
      resourceUrl(
        "http://user@localhost:3000/model.glb",
        "http://localhost:3000/viewer/",
      ),
    /origin/,
  );
});

test("legacy classifier uses ordered name families, preserves labels, and leaves unknowns in Other", () => {
  const cases = [
    ["AM_Attack02_limitdodge", "Combat", "Attacks", "Ground"],
    ["AM_AirAttack_Start", "Combat", "Attacks", "Aerial"],
    ["AM_Rogue_AirSkill01", "Combat", "Skills", "Aerial"],
    ["AM_SkillQte01_Rogue", "Combat", "Skills", "QTE"],
    ["Stand1_Action01", "Idle & poses", "Idle", "Standing"],
    ["Cos_Pose", "Idle & poses", "Poses", "Presentation"],
    ["AM_Move_Limit_B", "Movement", "Locomotion", "Move"],
    ["Walk_F", "Movement", "Locomotion", "Walk"],
    ["Run", "Movement", "Locomotion", "Run"],
    ["Swim", "Movement", "Locomotion", "Swim"],
    ["AM_Jump_Second_F", "Traversal", "Jumping", "Jump"],
    ["FixHook_End_Fast", "Traversal", "Grappling", "Hook"],
    ["Playpiano_01_Montage.005", "Interactions", "Activities", "Music"],
    ["SitTalk01_Loop", "Interactions", "Posture", "Sitting"],
    ["Ui_Weapon_Start", "Technical", "Presentation", "UI"],
    ["M0246_Cam025_R2T1ShouanrenMd10011", "Technical", "Cinematics", "Camera"],
    ["Guidi01", "Other", "Other", "Other"],
    ["NotAnAttack01", "Other", "Other", "Other"],
    ["Runaway", "Other", "Other", "Other"],
  ];
  for (const [name, category, group, subgroup] of cases) {
    assert.deepEqual(classifyAnimation(name), {
      category,
      group,
      subgroup,
      label: name,
    });
  }
  for (const value of [null, undefined, {}, [], 253, "", "  "]) {
    assert.deepEqual(classifyAnimation(value), {
      category: "Other",
      group: "Other",
      subgroup: "Other",
      label: "Unnamed animation",
    });
  }
});

test("binding attaches indexed imported metadata without changing source identity or target playback", () => {
  const pack = rig(),
    preview = rig({ suffix: "_1" });
  // Identical names deliberately demonstrate index lookup, not a name lookup.
  pack.animations.push(pack.animations[0].clone());
  const catalogs = [
    {
      category: "Imported",
      group: "First",
      subgroup: "Sequence",
      label: "Custom label",
    },
    {
      category: "Imported",
      group: "Second",
      subgroup: "Montage",
      label: "Another label",
    },
  ];
  pack.parser.json.animations = catalogs.map((catalog) => ({
    extras: { solaris_atlas: catalog },
  }));
  pack.animations[0].userData = { exporter: "keep" };
  const clips = bindAnimationClips(pack, preview);
  clips.forEach((clip, index) => {
    assert.deepEqual(clip.userData.catalog, catalogs[index]);
    assert.notEqual(clip.userData.catalog, catalogs[index]);
    assert.equal(clip.name, pack.animations[index].name);
    assert.equal(clip.duration, pack.animations[index].duration);
    assert.equal(clip.tracks[0].name, `${preview.bone.uuid}.position`);
    assert.deepEqual(
      clip.tracks[0].values,
      pack.animations[index].tracks[0].values,
    );
    assert.equal(pack.animations[index].tracks[0].name, "Hips.position");
    assert.equal(pack.animations[index].userData?.catalog, undefined);
  });
  assert.equal(clips[0].userData.exporter, "keep");
  const mixer = new THREE.AnimationMixer(preview.scene);
  mixer.clipAction(clips[0]).play();
  mixer.update(0.5);
  assert.equal(preview.bone.position.x, 1);
});

test("embedded helper and bound clips use legacy fallback for missing or malformed extras", () => {
  for (const metadata of [
    undefined,
    null,
    [],
    "bad",
    {},
    { category: "Combat" },
    { category: "Combat", group: [], subgroup: "Ground", label: "x" },
    { category: "Combat", group: "Attacks", subgroup: " ", label: "x" },
  ]) {
    const pack = rig(),
      preview = rig();
    pack.parser.json.animations = [{ extras: { solaris_atlas: metadata } }];
    assert.deepEqual(
      bindAnimationClips(pack, preview)[0].userData.catalog,
      classifyAnimation("Stand1"),
    );
    assert.equal(
      attachAnimationCatalog(pack.animations[0], pack, 0),
      pack.animations[0],
    );
    assert.deepEqual(
      pack.animations[0].userData.catalog,
      classifyAnimation("Stand1"),
    );
  }
  const pack = rig();
  assert.deepEqual(
    bindAnimationClips(pack, rig())[0].userData.catalog,
    classifyAnimation("Stand1"),
  );
});

test("pack is never requested on initialization; clicks deduplicate and failures allow retry", async () => {
  let requests = 0,
    applications = 0;
  const load = lazyAnimationPack(
    "/animations.glb",
    async (url) => {
      assert.equal(url, "/animations.glb");
      if (++requests === 1) throw new Error("offline");
      return "pack";
    },
    (pack) => {
      assert.equal(pack, "pack");
      applications++;
    },
  );
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(requests, 0);
  const first = load();
  assert.equal(load(), first);
  await assert.rejects(first, /offline/);
  await load();
  await load();
  assert.equal(requests, 2);
  assert.equal(applications, 1);
});

test("authored names bind to existing bones despite loader suffixes; missing/ambiguous rigs fail", () => {
  const pack = rig(),
    preview = rig({ suffix: "_1" });
  const clips = bindAnimationClips(pack, preview);
  assert.equal(clips[0].tracks[0].name, `${preview.bone.uuid}.position`);
  assert.equal(pack.animations[0].tracks[0].name, "Hips.position");
  const mixer = new THREE.AnimationMixer(preview.scene);
  mixer.clipAction(clips[0]).play();
  mixer.update(0.5);
  assert.equal(preview.bone.position.x, 1);
  assert.equal(pack.bone.position.x, 0);
  for (const options of [{ duplicate: true }, { missing: true }]) {
    assert.throws(() => bindAnimationClips(pack, rig(options)), /target/);
  }
  assert.throws(
    () => bindAnimationClips({ ...pack, animations: [] }, preview),
    /no clips/,
  );
});

// Execute the real viewer orchestration with only browser/renderer boundaries
// stubbed. Playback uses the real Three.js mixer and bone objects.
