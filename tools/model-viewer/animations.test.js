import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import * as THREE from "three";
import { BoneCamera, cameraBones } from "./camera-rig.js";
import { RootMotionLock } from "./root-motion.js";
import {
  resourceUrl,
  animationLoadLabel,
  bindAnimationClips,
  lazyAnimationPack,
  classifyAnimation,
  attachAnimationCatalog,
  prepareAnimatedMeshes,
} from "./animations.js";

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
async function viewer(search, { embedded = false } = {}) {
  const elements = new Map();
  const element = (id) => {
    if (!elements.has(id))
      elements.set(id, {
        hidden: false,
        disabled: false,
        checked: false,
        value: "1",
        setAttribute() {},
        removeAttribute() {},
        addEventListener() {},
        append() {},
        replaceChildren(...children) {
          this.children = children;
        },
        closest() {
          return element(`${id}-label`);
        },
      });
    return elements.get(id);
  };
  element("load-animations").hidden = true;
  const preview = rig(),
    pack = rig();
  if (!embedded) preview.animations = [];
  const requests = [];
  let fail = false;
  class Loader {
    async loadAsync(url) {
      requests.push(url);
      if (url.includes("pack")) {
        if (fail) throw new Error("offline");
        return pack;
      }
      return preview;
    }
  }
  class Renderer {
    domElement = element("canvas");
    setPixelRatio() {}
    setAnimationLoop() {}
  }
  class Controls {
    target = new THREE.Vector3();
    update() {}
  }
  class Springs {
    joints = [1];
    missing = [];
    reset() {}
  }
  const code = (
    await readFile(new URL("./viewer.js", import.meta.url), "utf8")
  ).replace(/^import[\s\S]*?;\r?\n/gm, "");
  vm.runInNewContext(code, {
    THREE: { ...THREE, WebGLRenderer: Renderer },
    GLTFLoader: Loader,
    OrbitControls: Controls,
    SpringBones: Springs,
    BoneCamera,
    cameraBones,
    RootMotionLock,
    validatedResourceUrl: resourceUrl,
    animationLoadLabel,
    attachAnimationCatalog,
    bindAnimationClips,
    lazyAnimationPack,
    prepareAnimatedMeshes,
    createViewerUI: () => ({
      setAnimations(clips, onSelect, initial) {
        element("animations").value = initial;
      },
      selectAnimation(name) {
        element("animations").value = name;
      },
      setBones() {},
      selectBone() {},
    }),
    document: {
      body: { dataset: {} },
      getElementById: element,
      querySelector: () => element("h1"),
      addEventListener() {},
    },
    location: {
      href: `https://atlas.test/viewer/${search}`,
      origin: "https://atlas.test",
      search,
    },
    URLSearchParams,
    devicePixelRatio: 1,
    matchMedia: () => ({ matches: true }),
    ResizeObserver: class {
      observe() {}
    },
    Option: class {
      constructor(text, value) {
        this.text = text;
        this.value = value;
      }
    },
    fetch: async (url) => {
      requests.push(url);
      return { ok: true, json: async () => ({}) };
    },
  });
  await new Promise((resolve) => setImmediate(resolve));
  return {
    element,
    requests,
    setFailure: (value) => {
      fail = value;
    },
  };
}

test("viewer stays static until click, retries, installs Stand1 and preserves physics", async () => {
  const { element, requests, setFailure } = await viewer(
    "?model=/model.glb&physics=/physics.json&animations=/pack.glb&animation_bytes=1048576",
  );
  assert.deepEqual(requests, [
    "https://atlas.test/model.glb",
    "https://atlas.test/physics.json",
  ]);
  assert.equal(element("playback").hidden, true);
  assert.equal(element("load-animations").hidden, false);
  assert.equal(
    element("load-animations").textContent,
    "Load animations (~1.0 MiB)",
  );
  element("physics").checked = true;
  element("turntable").checked = true;
  setFailure(true);
  const attempt = element("load-animations").onclick();
  assert.equal(element("load-animations").disabled, true);
  assert.equal(element("playback").disabled, true);
  await element("load-animations").onclick(); // ignored while busy
  await attempt;
  assert.match(element("animation-status").textContent, /offline/);
  assert.equal(element("load-animations").disabled, false);
  assert.equal(requests.filter((url) => url.includes("pack")).length, 1);
  setFailure(false);
  await element("load-animations").onclick();
  assert.equal(element("playback").hidden, false);
  assert.equal(element("playback").disabled, false);
  assert.equal(element("animations").value, "Stand1");
  assert.equal(element("play").textContent, "Play"); // reduced motion
  assert.equal(element("turntable").checked, false);
  assert.equal(element("turntable-label").hidden, true);
  assert.equal(element("physics").checked, true);
  assert.equal(element("physics").disabled, false);
  assert.equal(element("load-animations").hidden, true);
});

test("embedded clips remain available without a pack; unsafe pack URLs never reach loader", async () => {
  const local = await viewer("", { embedded: true });
  assert.equal(local.element("animations").value, "Stand1");
  assert.equal(local.element("playback").hidden, false);
  assert.equal(local.element("load-animations").hidden, true);
  const unsafe = await viewer(
    "?model=/model.glb&animations=https://other.test/pack.glb",
  );
  await unsafe.element("load-animations").onclick();
  assert.match(unsafe.element("animation-status").textContent, /viewer origin/);
  assert.equal(unsafe.requests.length, 1);
});

test("transparent profile preview requests only its rig, never physics or animation packs", async () => {
  const preview = await viewer(
    "?mode=preview&model=/model.glb&physics=/physics.json&animations=/pack.glb",
  );
  assert.deepEqual(preview.requests, ["https://atlas.test/model.glb"]);
  assert.equal(preview.element("playback").hidden, true);
  assert.equal(preview.element("load-animations").hidden, true);
  assert.equal(preview.element("status").textContent, "Model ready");
});

test('animated parts stay eligible at the viewport edge despite stale bind-pose bounds', () => {
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute([0, 0, 0, 1, 0, 0, 0, 1, 0], 3));
  geometry.setAttribute('skinIndex', new THREE.Uint16BufferAttribute(new Array(12).fill(0), 4));
  geometry.setAttribute('skinWeight', new THREE.Float32BufferAttribute([1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0], 4));
  const scene = new THREE.Group(), part = new THREE.SkinnedMesh(geometry, new THREE.MeshBasicMaterial());
  const bone = new THREE.Bone(), floor = new THREE.Mesh(new THREE.PlaneGeometry());
  scene.add(part, floor); part.add(bone); part.bind(new THREE.Skeleton([bone]));
  scene.updateMatrixWorld(true); part.computeBoundingSphere();
  bone.position.x = 10; scene.updateMatrixWorld(true);
  const camera = new THREE.PerspectiveCamera(35, 1, 0.01, 100);
  camera.position.set(10, 0, 3); camera.lookAt(10, 0, 0); camera.updateMatrixWorld(true);
  const frustum = new THREE.Frustum().setFromProjectionMatrix(new THREE.Matrix4().multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse));
  assert(frustum.containsPoint(part.getVertexPosition(0, new THREE.Vector3())));
  assert.equal(frustum.intersectsObject(part), false, 'bind bounds incorrectly reject visible animated vertices');
  prepareAnimatedMeshes(scene);
  assert.equal(part.frustumCulled, false);
  assert.equal(!part.frustumCulled || frustum.intersectsObject(part), true);
  assert.equal(floor.frustumCulled, true);
});
