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

test('a component completing after viewer disposal releases its rig without reporting', async () => {
  const child = rig('Aki/Prop/Stand'), root = rig('Aki/Body/Stand');
  let finish, reports = 0, disposed = 0;
  const linked = createLinkedModels({ assets: { id: 'root', linked_models: [{ id: 'prop', parent_id: 'root', label: 'Prop', preview: asset('child'), component: {} }] },
    load: () => new Promise(resolve => { finish = resolve; }), dispose: () => disposed++, report: () => reports++, getRoot: () => root.scene, getAction: () => null });
  linked.start(); linked.dispose(); const before = reports;
  finish(child); await tick();
  assert.equal(disposed, 1); assert.equal(reports, before); assert.equal(child.scene.parent, null);
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
