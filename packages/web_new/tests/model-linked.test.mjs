import test from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import { createLinkedModels, packagePath, componentMatrix } from '../src/components/models/engine/linked-models.js';

const tick = () => new Promise(resolve => setImmediate(resolve));
const asset = url => ({ url });
function rig(source, name = 'Joint') {
  const scene = new THREE.Group(), bone = new THREE.Bone(); bone.name = name; scene.add(bone);
  const clip = new THREE.AnimationClip('Stand1', 2, [new THREE.VectorKeyframeTrack(`${name}.position`, [0, 2], [0, 0, 0, 2, 0, 0])]);
  const parser = { associations: new Map([[bone, { nodes: 0 }]]), json: { nodes: [{ name }], animations: [{ extras: { solaris_atlas: { source } } }] } };
  return { scene, bone, parser, animations: [clip] };
}

test('component rigs attach to authored socket and follow exact package-bound parent time', async () => {
  const body = rig('Client/Content/Aki/Body/Stand.json', 'Hand');
  const child = rig('Client/Content/Aki/Prop/Stand.json');
  const pack = rig('Client/Content/Aki/Prop/Stand.json');
  body.animations[0].userData = { sourcePackage: 'Client/Content/Aki/Body/Stand.json' };
  const mixer = new THREE.AnimationMixer(body.scene), action = mixer.clipAction(body.animations[0]).play();
  const requests = [], disposed = [], states = [];
  const linked = createLinkedModels({ assets: { id: 'root', scene: { components: ['prop'] }, linked_models: [{ id: 'prop', parent_id: 'root', label: 'Prop', preview: asset('child'), animations: asset('pack'), component: {
    socket: 'Socket', socket_transform: { bone: 'Hand', location: { X: 100 } }, transform: { location: { Z: 50 } },
    bindings: [{ parent_clip: '/Game/Aki/Body/Stand.Stand', child_clip: '/Game/Aki/Prop/Stand.Stand' }],
  } }] }, load: async url => { requests.push(url); return url === 'pack' ? pack : child; }, dispose: obj => disposed.push(obj), report: state => states.push(state), getRoot: () => body.scene, getAction: () => action });
  assert.deepEqual(requests, []);
  linked.start(); await tick();
  assert.deepEqual(requests, ['child', 'pack']);
  assert.equal(child.scene.parent, body.bone);
  assert(Math.abs(child.scene.position.x - 1) < 1e-6); assert(Math.abs(child.scene.position.y - 0.5) < 1e-6);
  action.time = 1; linked.sync(); assert(Math.abs(child.bone.position.x - 1) < 1e-6);
  action.time = 0.25; linked.sync(); assert(Math.abs(child.bone.position.x - 0.25) < 1e-6);
  action.time = 2.25; linked.sync(); assert(Math.abs(child.bone.position.x - 0.25) < 1e-6);
  linked.set('prop', false); assert.equal(child.scene.visible, false);
  linked.dispose(); assert.equal(child.scene.parent, null); assert(disposed.includes(child.scene)); assert(disposed.includes(pack.scene));
  assert.equal(states.at(-1)[0].visible, false);
});

test('ambiguous sources do not bind, failures retry, and baked components stay unloaded', async () => {
  const body = rig('Aki/Body/Stand', 'Hand'), child = rig('Aki/Prop/Stand');
  body.animations[0].userData = { sourcePackage: 'Aki/Other/Stand' };
  const action = new THREE.AnimationMixer(body.scene).clipAction(body.animations[0]).play();
  let attempts = 0, state;
  const linked = createLinkedModels({ assets: { id: 'root', linked_models: [
    { id: 'baked', parent_id: 'root', label: 'Baked', preview: asset('baked'), component: { baked: true } },
    { id: 'prop', parent_id: 'root', label: 'Prop', preview: asset('child'), component: { default_visible: false, bindings: [{ parent_clip: 'Aki/Body/Stand', child_clip: 'Aki/Prop/Stand' }] } },
  ] }, load: async () => { if (++attempts === 1) throw new Error('offline'); return child; }, dispose() {}, report: value => { state = value; }, getRoot: () => body.scene, getAction: () => action });
  linked.start(); assert.equal(attempts, 0); assert.equal(state.length, 1);
  linked.set('prop', true); await tick(); assert.equal(state[0].error, 'offline');
  linked.set('prop', true); await tick(); assert.equal(attempts, 2); assert.equal(state[0].error, '');
  action.time = 1; linked.sync(); assert.equal(child.bone.position.x, 0);
  linked.dispose();
});

test('source normalization retains full package identity and converts Unreal units', () => {
  assert.equal(packagePath('Client/Content/Aki/One/Stand.json'), 'Aki/One/Stand');
  assert.equal(packagePath('/Game/Aki/One/Stand.Stand'), 'Aki/One/Stand');
  assert.notEqual(packagePath('/Game/Aki/Two/Stand.Stand'), packagePath('Aki/One/Stand'));
  const position = new THREE.Vector3().setFromMatrixPosition(componentMatrix({ location: { X: 100, Y: 200, Z: 300 } }));
  assert(position.distanceTo(new THREE.Vector3(1, 3, 2)) < 1e-6);
});

test('Unreal quaternion reflection matches the converter basis and rotator axes', () => {
  const half = Math.sin(Math.PI / 4), w = Math.cos(Math.PI / 4);
  const reflect = new THREE.Matrix4().makeScale(1, -1, 1);
  const basis = new THREE.Matrix4().makeRotationX(-Math.PI / 2);
  for (const [rotation, rotator] of [
    [{ X: -half, Y: 0, Z: 0, W: w }, { Roll: 90 }],
    [{ X: 0, Y: -half, Z: 0, W: w }, { Pitch: 90 }],
    [{ X: 0, Y: 0, Z: half, W: w }, { Yaw: 90 }],
  ]) {
    const raw = new THREE.Matrix4().makeRotationFromQuaternion(new THREE.Quaternion(rotation.X, rotation.Y, rotation.Z, rotation.W));
    const expected = basis.clone().multiply(reflect).multiply(raw).multiply(reflect).multiply(basis.clone().invert());
    const actual = componentMatrix({ rotation });
    const fromRotator = componentMatrix({ rotation: rotator });
    for (let i = 0; i < 16; i++) {
      assert(Math.abs(actual.elements[i] - expected.elements[i]) < 1e-9);
      assert(Math.abs(actual.elements[i] - fromRotator.elements[i]) < 1e-9);
    }
  }
});

test('a component completing after viewer disposal releases its rig without reporting', async () => {
  const child = rig('Aki/Prop/Stand'), root = rig('Aki/Body/Stand');
  let finish, reports = 0, disposed = 0;
  const linked = createLinkedModels({ assets: { id: 'root', linked_models: [{ id: 'prop', parent_id: 'root', label: 'Prop', preview: asset('child'), component: {} }] },
    load: () => new Promise(resolve => { finish = resolve; }), dispose: () => disposed++, report: () => reports++, getRoot: () => root.scene, getAction: () => null });
  linked.start(); linked.dispose(); const before = reports;
  finish(child); await tick();
  assert.equal(disposed, 1); assert.equal(reports, before); assert.equal(child.scene.parent, null);
});

test('unchanged animation frames do not report UI state; visibility and override changes do', async () => {
  const body = rig('Aki/Body/Idle'), child = rig('Aki/Prop/Idle');
  body.animations[0].userData = { sourcePackage: 'Aki/Body/Idle' };
  const action = new THREE.AnimationMixer(body.scene).clipAction(body.animations[0]).play();
  const reports = [];
  const linked = createLinkedModels({ assets: { id: 'root', linked_models: [{ id: 'prop', parent_id: 'root', label: 'Prop', preview: asset('child'), component: { default_visible: true } }] },
    load: async () => child, dispose() {}, report: state => reports.push(state), getRoot: () => body.scene, getAction: () => action });
  linked.start(); await tick();
  const before = reports.length;
  for (let i = 0; i < 300; i++) { action.time = i / 100; linked.sync(); }
  assert.equal(reports.length, before);
  linked.set('prop', true); assert.equal(reports.length, before + 1);
  assert.equal(reports.at(-1)[0].override, true);
  linked.set('prop', false); assert.equal(reports.length, before + 2);
  assert.equal(reports.at(-1)[0].visible, false);
  linked.set('prop', false); assert.equal(reports.length, before + 2);
  linked.dispose();
});

test('visibility cues follow the exact source clip on seek and preserve the user switch', async () => {
  const child = rig('Aki/Prop/Stand'), body = rig('Aki/Body/Stand');
  body.animations[0].userData = { sourcePackage: 'Client/Content/Aki/Body/Stand.json' };
  const action = new THREE.AnimationMixer(body.scene).clipAction(body.animations[0]).play();
  const linked = createLinkedModels({ assets: { id: 'root', linked_models: [{ id: 'prop', parent_id: 'root', label: 'Prop', preview: asset('child'), component: { cues: [{
    kind: 'TsAnimNotifyStateSubMeshControl_C', source_clip: 'Aki/Body/Stand', start: 0.5, end: 1.5,
    defaults: { begin_visible: false, end_visible: true, begin_delay: 0, end_delay: 0 },
  }] } }] }, load: async () => child, dispose() {}, report() {}, getRoot: () => body.scene, getAction: () => action });
  linked.start(); await tick();
  action.time = 1; linked.sync(); assert.equal(child.scene.visible, false);
  action.time = 1.75; linked.sync(); assert.equal(child.scene.visible, true);
  action.time = 0.25; linked.sync(); assert.equal(child.scene.visible, true);
  linked.set('prop', false); action.time = 1.75; linked.sync(); assert.equal(child.scene.visible, false);
  linked.dispose();
});

test('Hsin cues lazily reveal the fox, move its socket, and hide only body geometry', async () => {
  const body = rig('Aki/Hsin/Stand', 'Hand'), fox = rig('Aki/Hsin/Fox');
  const second = new THREE.Bone(); second.name = 'Head'; body.scene.add(second);
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshBasicMaterial()); body.scene.add(mesh);
  body.animations[0].userData = { sourcePackage: 'Client/Content/Aki/Hsin/Stand.json' };
  const action = new THREE.AnimationMixer(body.scene).clipAction(body.animations[0]).play();
  const cues = [
    { kind: 'TsAnimNotifyStateHideMesh_C', source_clip: 'Aki/Hsin/Stand', start: 0.5, end: 1.5, defaults: { target: 'notifying_mesh', hide: true, hide_children: false } },
    { kind: 'TsAnimNotifyStateSubMeshControl_C', source_clip: 'Aki/Hsin/Stand', start: 0.5, end: 1.5, defaults: { begin_visible: true, end_visible: true, begin_delay: 0, end_delay: 0 } },
    { kind: 'TsAnimNotifyStateChangeSlot_C', source_clip: 'Aki/Hsin/Stand', start: 0.5, end: 1.5, properties: { SwitchToSlotName: 'FoxSocket', SlotTransform: { Translation: { X: 100 }, Rotation: { Pitch: 0, Yaw: 0, Roll: 0 }, Scale3D: { X: 1, Y: 1, Z: 1 } } }, socket_transform: { bone: 'Head', location: { Z: 50 } } },
  ];
  let loads = 0, state;
  const linked = createLinkedModels({ assets: { id: 'root', linked_models: [{ id: 'fox', parent_id: 'root', label: 'Fox', preview: asset('fox'), component: { default_visible: false, socket: 'Hand', cues } }] },
    load: async () => { loads++; return fox; }, dispose() {}, report: value => { state = value; }, getRoot: () => body.scene, getAction: () => action });
  linked.start(); await tick(); assert.equal(loads, 0);
  action.time = 0.75; linked.sync(); await tick();
  assert.equal(loads, 1); assert.equal(fox.scene.parent, second); assert.equal(mesh.layers.mask, 0);
  assert.equal(fox.scene.visible, true); assert(Math.abs(fox.scene.position.x - 1) < 1e-6); assert(Math.abs(fox.scene.position.y - 0.5) < 1e-6);
  action.time = 1.75; linked.sync(); assert.equal(mesh.layers.mask, 1); assert.equal(fox.scene.parent, body.bone); assert.equal(fox.scene.visible, true);
  action.time = 0.1; linked.sync(); assert.equal(fox.scene.visible, false); assert.equal(state[0].visible, false);
  linked.set('fox', true); assert.equal(fox.scene.visible, true);
  linked.set('fox', null); assert.equal(fox.scene.visible, false);
  linked.dispose();
});

test('Sigrika effect is loaded on demand and follows notify-local time without wrapping', async () => {
  const body = rig('Aki/Sigrika/Stand', 'Hand'), effect = rig('Aki/Effect/Idle');
  body.animations[0].userData = { sourcePackage: 'Aki/Sigrika/Stand' };
  const action = new THREE.AnimationMixer(body.scene).clipAction(body.animations[0]).play();
  let requests = 0;
  const linked = createLinkedModels({ assets: { id: 'root', linked_models: [{ id: 'effect', parent_id: 'root', label: 'Effect', preview: asset('effect'), component: { kind: 'effect', default_visible: false,
    bindings: [{ kind: 'effect', parent_clip: 'Aki/Sigrika/Stand', child_clip: 'Aki/Effect/Idle', source: { looping: false } }],
    cues: [{ kind: 'AnimNotifyStateEffect_C', source_clip: 'Aki/Sigrika/Stand', start: 0.25, end: 3, properties: { PlayRate: 2 } }],
  } }] }, load: async () => { requests++; return effect; }, dispose() {}, report() {}, getRoot: () => body.scene, getAction: () => action });
  linked.start(); await tick(); assert.equal(requests, 0);
  action.time = 0.75; linked.sync(); await tick(); assert.equal(requests, 1); assert.equal(effect.scene.visible, true); assert.equal(effect.bone.position.x, 1);
  action.time = 1.75; linked.sync(); assert.equal(effect.bone.position.x, 2);
  action.time = 0.5; linked.sync(); assert.equal(effect.bone.position.x, 0.5);
  linked.set('effect', true); action.time = 3.1; linked.sync(); assert.equal(effect.scene.visible, false);
  action.time = 0.2; linked.sync(); assert.equal(effect.scene.visible, false);
  linked.dispose();
});

test('root-only hide cues restore body layers and do not hide attached children', () => {
  const body = rig('Aki/Body/Idle'), mesh = new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshBasicMaterial());
  const attachment = new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshBasicMaterial());
  body.scene.add(mesh); body.bone.add(attachment); attachment.userData.accessory_name = 'Baked prop';
  body.animations[0].userData = { sourcePackage: 'Aki/Body/Idle' };
  const action = new THREE.AnimationMixer(body.scene).clipAction(body.animations[0]).play();
  const linked = createLinkedModels({ assets: { id: 'root', scene: { cues: [{ kind: 'TsAnimNotifyStateHideMesh_C', source_clip: 'Aki/Body/Idle', start: 0, end: 1, defaults: { target: 'notifying_mesh', hide: true, hide_children: false } }] } },
    load() {}, dispose() {}, report() {}, getRoot: () => body.scene, getAction: () => action });
  linked.start(); assert.equal(mesh.layers.mask, 0); assert.equal(attachment.layers.mask, 1);
  action.time = 1; linked.sync(); assert.equal(mesh.layers.mask, 1); linked.dispose();
});

test('hidden root geometry restores its original layers on replacement and disposal', () => {
  const first = rig('Aki/Body/Idle'), second = rig('Aki/Body/Idle');
  for (const body of [first, second]) { body.mesh = new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshBasicMaterial()); body.mesh.layers.mask = 5; body.scene.add(body.mesh); }
  first.animations[0].userData = { sourcePackage: 'Aki/Body/Idle' };
  const action = new THREE.AnimationMixer(first.scene).clipAction(first.animations[0]).play();
  let current = first.scene;
  const linked = createLinkedModels({ assets: { id: 'root', scene: { cues: [{ kind: 'TsAnimNotifyStateHideMesh_C', source_clip: 'Aki/Body/Idle', start: 0, end: 1, defaults: { target: 'notifying_mesh', hide: true, hide_children: false } }] } },
    load() {}, dispose() {}, report() {}, getRoot: () => current, getAction: () => action });
  linked.start(); assert.equal(first.mesh.layers.mask, 0);
  current = second.scene; linked.sync(); assert.equal(first.mesh.layers.mask, 5); assert.equal(second.mesh.layers.mask, 0);
  linked.dispose(); assert.equal(second.mesh.layers.mask, 5);
});

test('a hidden linked rig preloads when its exact binding becomes active', async () => {
  const body = rig('Aki/Body/Idle'), child = rig('Aki/Fox/Idle');
  body.animations[0].userData = { sourcePackage: 'Aki/Other/Idle' };
  const action = new THREE.AnimationMixer(body.scene).clipAction(body.animations[0]).play();
  let requests = 0;
  const linked = createLinkedModels({ assets: { id: 'root', linked_models: [{ id: 'fox', parent_id: 'root', label: 'Fox', preview: asset('fox'), component: { default_visible: false, bindings: [{ parent_clip: 'Aki/Body/Idle', child_clip: 'Aki/Fox/Idle' }] } }] },
    load: async () => { requests++; return child; }, dispose() {}, report() {}, getRoot: () => body.scene, getAction: () => action });
  linked.start(); await tick(); assert.equal(requests, 0);
  action.getClip().userData.sourcePackage = 'Aki/Body/Idle'; linked.sync(); await tick();
  assert.equal(requests, 1); assert.equal(child.scene.visible, false); linked.dispose();
});

test('montage segments map source-local cue windows and effect playback rate', async () => {
  const body = rig('Aki/Body/Montage'), effect = rig('Aki/Effect/Idle');
  body.animations[0].userData = { sourcePackage: 'Aki/Body/Montage' };
  const action = new THREE.AnimationMixer(body.scene).clipAction(body.animations[0]).play();
  const cue = { kind: 'AnimNotifyStateEffect_C', source_clip: 'Aki/Body/Idle', montage: 'Aki/Body/Montage',
    segment_start: 2, source_start: 1, source_end: 3, play_rate: 2, loop_count: 2, start: 1.5, end: 2.5 };
  const linked = createLinkedModels({ assets: { id: 'root', linked_models: [{ id: 'effect', parent_id: 'root', label: 'Effect', preview: asset('effect'), component: { kind: 'effect', default_visible: false, cues: [cue], bindings: [{ parent_clip: 'Aki/Body/Idle', child_clip: 'Aki/Effect/Idle', source: { looping: false } }] } }] },
    load: async () => effect, dispose() {}, report() {}, getRoot: () => body.scene, getAction: () => action });
  linked.start(); action.time = 2.5; linked.sync(); await tick();
  assert.equal(effect.scene.visible, true); assert.equal(effect.bone.position.x, 0.5);
  action.time = 2.8; linked.sync(); assert.equal(effect.scene.visible, false);
  action.time = 3.5; linked.sync(); assert.equal(effect.scene.visible, true); assert.equal(effect.bone.position.x, 0.5);
  linked.dispose();
});
