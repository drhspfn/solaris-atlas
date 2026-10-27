import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';
import * as THREE from 'three';
import { resourceUrl, animationLoadLabel, bindAnimationClips, lazyAnimationPack } from './animations.js';

function rig({ suffix = '', duplicate = false, missing = false } = {}) {
  const scene = new THREE.Group();
  const bone = new THREE.Bone();
  bone.name = `Hips${suffix}`;
  scene.add(bone);
  const associations = new Map([[bone, { nodes: 0 }]]);
  if (duplicate) {
    const other = new THREE.Bone(); scene.add(other);
    associations.set(other, { nodes: 0 });
  }
  return { scene, bone, parser: { associations, json: { nodes: [{ name: missing ? 'Other' : 'Hips' }] } },
    animations: [new THREE.AnimationClip('Stand1', 1, [
      new THREE.VectorKeyframeTrack(`${bone.name}.position`, [0, 1], [0, 0, 0, 2, 0, 0]),
    ])] };
}

test('same-origin HTTP(S) validation and optional integer byte hint', () => {
  const base = 'https://atlas.test/viewer/index.html';
  assert.equal(resourceUrl('../animations.glb', base), 'https://atlas.test/animations.glb');
  for (const value of ['https://elsewhere.test/a.glb', '//elsewhere.test/a', 'javascript:alert(1)', 'data:,bad', 'blob:https://atlas.test/id', 'http://atlas.test/a']) {
    assert.throws(() => resourceUrl(value, base), /viewer origin/);
  }
  assert.equal(animationLoadLabel('10485760'), 'Load animations (~10.0 MiB)');
  for (const value of [null, '', '-1', 'NaN', '1.5', 'Infinity', '1e6', '9007199254740992']) {
    assert.equal(animationLoadLabel(value), 'Load animations');
  }
});

test('pack is never requested on initialization; clicks deduplicate and failures allow retry', async () => {
  let requests = 0, applications = 0;
  const load = lazyAnimationPack('/animations.glb', async url => {
    assert.equal(url, '/animations.glb');
    if (++requests === 1) throw new Error('offline');
    return 'pack';
  }, pack => { assert.equal(pack, 'pack'); applications++; });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(requests, 0);
  const first = load(); assert.equal(load(), first);
  await assert.rejects(first, /offline/);
  await load(); await load();
  assert.equal(requests, 2); assert.equal(applications, 1);
});

test('authored names bind to existing bones despite loader suffixes; missing/ambiguous rigs fail', () => {
  const pack = rig(), preview = rig({ suffix: '_1' });
  const clips = bindAnimationClips(pack, preview);
  assert.equal(clips[0].tracks[0].name, `${preview.bone.uuid}.position`);
  assert.equal(pack.animations[0].tracks[0].name, 'Hips.position');
  const mixer = new THREE.AnimationMixer(preview.scene);
  mixer.clipAction(clips[0]).play(); mixer.update(0.5);
  assert.equal(preview.bone.position.x, 1);
  assert.equal(pack.bone.position.x, 0);
  for (const options of [{ duplicate: true }, { missing: true }]) {
    assert.throws(() => bindAnimationClips(pack, rig(options)), /target/);
  }
  assert.throws(() => bindAnimationClips({ ...pack, animations: [] }, preview), /no clips/);
});

// Execute the real viewer orchestration with only browser/renderer boundaries
// stubbed. Playback uses the real Three.js mixer and bone objects.
async function viewer(search, { embedded = false } = {}) {
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, {
      hidden: false, disabled: false, checked: false, value: '1',
      setAttribute() {}, removeAttribute() {}, addEventListener() {}, append() {},
      replaceChildren(...children) { this.children = children; },
      closest() { return element(`${id}-label`); },
    });
    return elements.get(id);
  };
  element('load-animations').hidden = true;
  const preview = rig(), pack = rig();
  if (!embedded) preview.animations = [];
  const requests = [];
  let fail = false;
  class Loader {
    async loadAsync(url) {
      requests.push(url);
      if (url.includes('pack')) {
        if (fail) throw new Error('offline');
        return pack;
      }
      return preview;
    }
  }
  class Renderer {
    domElement = element('canvas');
    setPixelRatio() {} setAnimationLoop() {}
  }
  class Controls {
    target = new THREE.Vector3(); update() {}
  }
  class Springs { joints = [1]; missing = []; reset() {} }
  const code = (await readFile(new URL('./viewer.js', import.meta.url), 'utf8')).replace(/^import .*;\r?\n/gm, '');
  vm.runInNewContext(code, {
    THREE: { ...THREE, WebGLRenderer: Renderer }, GLTFLoader: Loader, OrbitControls: Controls,
    SpringBones: Springs, validatedResourceUrl: resourceUrl, animationLoadLabel, bindAnimationClips, lazyAnimationPack,
    document: { getElementById: element, querySelector: () => element('h1'), addEventListener() {} },
    location: { href: `https://atlas.test/viewer/${search}`, origin: 'https://atlas.test', search },
    URLSearchParams, devicePixelRatio: 1, matchMedia: () => ({ matches: true }),
    ResizeObserver: class { observe() {} }, Option: class { constructor(text, value) { this.text = text; this.value = value; } },
    fetch: async url => { requests.push(url); return { ok: true, json: async () => ({}) }; },
  });
  await new Promise(resolve => setImmediate(resolve));
  return { element, requests, setFailure: value => { fail = value; } };
}

test('viewer stays static until click, retries, installs Stand1 and preserves physics', async () => {
  const { element, requests, setFailure } = await viewer('?model=/model.glb&physics=/physics.json&animations=/pack.glb&animation_bytes=1048576');
  assert.deepEqual(requests, ['https://atlas.test/model.glb', 'https://atlas.test/physics.json']);
  assert.equal(element('playback').hidden, true);
  assert.equal(element('load-animations').hidden, false);
  assert.equal(element('load-animations').textContent, 'Load animations (~1.0 MiB)');
  element('physics').checked = true;
  element('turntable').checked = true;
  setFailure(true);
  const attempt = element('load-animations').onclick();
  assert.equal(element('load-animations').disabled, true);
  assert.equal(element('playback').disabled, true);
  await element('load-animations').onclick(); // ignored while busy
  await attempt;
  assert.match(element('animation-status').textContent, /offline/);
  assert.equal(element('load-animations').disabled, false);
  assert.equal(requests.filter(url => url.includes('pack')).length, 1);
  setFailure(false);
  await element('load-animations').onclick();
  assert.equal(element('playback').hidden, false);
  assert.equal(element('playback').disabled, false);
  assert.equal(element('animations').value, 'Stand1');
  assert.equal(element('play').textContent, 'Play'); // reduced motion
  assert.equal(element('turntable').checked, false);
  assert.equal(element('turntable-label').hidden, true);
  assert.equal(element('physics').checked, true);
  assert.equal(element('physics').disabled, false);
  assert.equal(element('load-animations').hidden, true);
});

test('embedded clips remain available without a pack; unsafe pack URLs never reach loader', async () => {
  const local = await viewer('', { embedded: true });
  assert.equal(local.element('animations').value, 'Stand1');
  assert.equal(local.element('playback').hidden, false);
  assert.equal(local.element('load-animations').hidden, true);
  const unsafe = await viewer('?model=/model.glb&animations=https://other.test/pack.glb');
  await unsafe.element('load-animations').onclick();
  assert.match(unsafe.element('animation-status').textContent, /viewer origin/);
  assert.equal(unsafe.requests.length, 1);
});
