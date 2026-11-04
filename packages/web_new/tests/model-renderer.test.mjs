import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';
import { readFile } from 'node:fs/promises';
import * as THREE from 'three';
import { BoneCamera, cameraBones } from '../src/components/models/engine/camera-rig.js';
import { RootMotionLock } from '../src/components/models/engine/root-motion.js';
import { FacialAnimation } from '../src/components/models/engine/facial.js';
import { bindAnimationClips, attachAnimationCatalog, prepareAnimatedMeshes } from '../src/components/models/engine/animations.js';

const flush = () => new Promise(resolve => setImmediate(resolve));
function rig(animated = false, facial = false) {
  const scene = new THREE.Group(), bone = new THREE.Bone();
  bone.name = 'Hips'; scene.add(bone);
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(1, 2, 1), new THREE.MeshBasicMaterial());
  scene.add(mesh);
  if (facial) { mesh.morphTargetDictionary = { E_Close: 0 }; mesh.morphTargetInfluences = [0]; }
  return { scene, mesh, bone, parser: { associations: new Map([[bone, { nodes: 0 }]]), json: { nodes: [{ name: 'Hips' }] } },
    animations: animated ? [new THREE.AnimationClip('Stand1', 1, [new THREE.VectorKeyframeTrack('Hips.position', [0, 1], [0, 0, 0, 2, 0, 0])])] : [] };
}
async function viewer({ preview = false, defer = false } = {}) {
  const requests = [], events = new Map(), changes = [];
  let frame, disconnected = false, destroyed = false, failure = false, finish;
  let geometryDisposals = 0;
  const low = rig(), full = rig(false, true), pack = rig(true);
  low.mesh.geometry.addEventListener('dispose', () => geometryDisposals++);
  class Renderer {
    domElement = { setAttribute() {}, addEventListener: (key, value) => events.set(key, value), removeEventListener: key => events.delete(key), remove() {} };
    setPixelRatio() {} setSize() {} render() {} dispose() { destroyed = true; } forceContextLoss() {}
    setAnimationLoop(fn) { frame = fn; }
  }
  class Controls {
    target = new THREE.Vector3(); update() {} dispose() {}
  }
  class Loader {
    async loadAsync(url) {
      requests.push(url);
      if (failure) throw new Error('offline');
      if (url.endsWith('/pack.glb')) return pack;
      if (url.endsWith('/full.glb')) return full;
      if (defer) await new Promise(resolve => { finish = resolve; });
      return low;
    }
  }
  const state = {};
  const code = (await readFile(new URL('../src/components/models/engine/viewer.js', import.meta.url), 'utf8'))
    .replace(/^import .*;\r?\n/gm, '').replace(/^export /gm, '');
  const context = { THREE: { ...THREE, WebGLRenderer: Renderer }, GLTFLoader: Loader, OrbitControls: Controls,
    SpringBones: class { joints = [1]; missing = []; reset() {} restore() {} update() {} },
    BoneCamera, cameraBones, RootMotionLock, FacialAnimation, bindAnimationClips, attachAnimationCatalog, prepareAnimatedMeshes,
    location: { href: 'https://atlas.test/characters/hero', protocol: 'https:' }, URL, AbortController,
    devicePixelRatio: 1, document: { hidden: false, addEventListener: (key, fn) => events.set(key, fn), removeEventListener: key => events.delete(key) },
    ResizeObserver: class { observe() {} disconnect() { disconnected = true; } },
    fetch: async url => { requests.push(url); return { ok: true, json: async () => url.endsWith('/face.json')
      ? { format: 'solaris-facial-v1', clips: [{ name: 'Stand1', body_clip: 'Stand1', seconds: 1,
        tracks: [{ target: 'E_Close', keys: [[0, 0], [0.5, 1], [1, 0]] }] }] } : {} }; },
  };
  vm.createContext(context); vm.runInContext(code, context);
  const asset = name => ({ url: `https://cdn.test/${name}`, size_bytes: 1000 });
  const settings = { playing: false, speed: 1, floor: true, turntable: false, physics: true, strength: 1,
    lock: false, axes: { x: true, y: false, z: true }, bone: '', follow: false, space: 'world', offset: [0, 0, 0] };
  const controller = context.createViewer({ append() {}, getBoundingClientRect: () => ({ width: 500, height: 400 }) },
    { preview: asset('low.glb'), download: asset('full.glb'), animations: asset('pack.glb'), physics: asset('physics.json'), facial: asset('face.json') },
    preview, settings, patch => { changes.push(patch); Object.assign(state, patch); });
  await flush();
  return { controller, settings, requests, state, full, low, changes, events,
    frame: ms => frame?.(ms), fail: value => { failure = value; }, finish: () => finish(),
    get disconnected() { return disconnected; }, get destroyed() { return destroyed; }, get disposedGeometry() { return geometryDisposals; } };
}

test('native renderer loads only compact geometry until explicit actions; retries keep working model', async () => {
  const v = await viewer();
  assert.equal(v.state.ready, true);
  assert.equal(v.requests.filter(url => url.endsWith('.glb')).length, 1);
  v.fail(true); await v.controller.command('animations');
  assert.equal(v.state.error, 'offline'); assert.equal(v.state.busy, false); assert.equal(v.state.ready, true);
  v.fail(false); await Promise.all([v.controller.command('animations'), v.controller.command('animations')]);
  assert.equal(v.requests.filter(url => url.endsWith('/pack.glb')).length, 1);
  assert.equal(v.state.fullQuality, true); assert.equal(v.state.facialLoaded, true);
  assert.equal(v.state.animation, 'Stand1'); assert.equal(v.state.animationsLoaded, true);
  assert.equal(v.disposedGeometry, 1, 'compact GPU geometry released after upgrade');
  v.controller.settings({ ...v.settings, playing: true }); v.frame(100); v.frame(200);
  assert(v.full.bone.position.x > 0, 'real mixer advances the bound full-model bone');
  v.controller.seek(0.5); assert.equal(v.state.time, 0.5);
  v.controller.dispose(); v.controller.dispose();
  assert.equal(v.disconnected, true); assert.equal(v.destroyed, true); assert.equal(v.events.size, 0);
});

test('preview never loads physics or packs; late loads release GPU resources after close', async () => {
  const preview = await viewer({ preview: true });
  assert.equal(preview.requests.length, 1); preview.controller.dispose();
  const pending = await viewer({ defer: true });
  pending.controller.dispose(); const count = pending.changes.length;
  pending.finish(); await flush();
  assert.equal(pending.changes.length, count, 'no callbacks after unmount');
  assert.equal(pending.disposedGeometry, 1);
});

test('context loss is reported and stops the render loop', async () => {
  const v = await viewer(); let prevented = false;
  v.events.get('webglcontextlost')({ preventDefault() { prevented = true; } });
  assert(prevented); assert.equal(v.state.ready, false); assert.match(v.state.error, /Graphics context lost/);
  v.controller.dispose();
});
