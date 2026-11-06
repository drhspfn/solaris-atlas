import * as THREE from 'three';
import { attachAnimationCatalog, bindAnimationClips, prepareAnimatedMeshes } from './animations.js';

// Compare package paths, never basenames: different packages may have the same clip name.
export function packagePath(value) {
  if (typeof value !== 'string') return '';
  return value.replace(/\\/g, '/').replace(/^\/+/, '').replace(/^Client\/Content\//i, '')
    .replace(/^Game\//i, '').replace(/\.json$/i, '').replace(/\.[^/]+$/, '');
}

function uniqueClip(clips, source) {
  const path = packagePath(source);
  if (!path) return undefined;
  const matches = clips.filter(clip => packagePath(clip.userData?.sourcePackage) === path);
  return matches.length === 1 ? matches[0] : undefined;
}

function cueVisibility(cues, parentAction) {
  const source = packagePath(parentAction?.getClip().userData?.sourcePackage);
  if (!source) return true;
  const events = [];
  for (const cue of cues || []) {
    if (cue.kind !== 'TsAnimNotifyStateSubMeshControl_C' || !cue.defaults) continue;
    const defaults = cue.defaults;
    const montage = packagePath(cue.montage);
    if ((montage || packagePath(cue.source_clip)) !== source) continue;
    const rate = montage ? cue.play_rate : 1;
    const start = montage ? cue.source_start : 0;
    const end = montage ? cue.source_end : 0;
    const loops = montage ? cue.loop_count : 1;
    if (!(rate > 0) || !Number.isFinite(cue.start) || !Number.isFinite(cue.end) ||
        !Number.isFinite(start) || !Number.isFinite(end) || !Number.isInteger(loops) || loops < 1 || loops > 1000) continue;
    for (let i = 0; i < loops; i++) {
      const map = time => montage ? cue.segment_start + i * (end - start) / rate + (time - start) / rate : time;
      for (const [time, delay, visible] of [[cue.start, defaults.begin_delay, defaults.begin_visible], [cue.end, defaults.end_delay, defaults.end_visible]]) {
        if (typeof visible !== 'boolean' || (montage && (time < start || time > end))) continue;
        const position = map(time + (Number.isFinite(delay) ? delay : 0));
        if (Number.isFinite(position) && position <= parentAction.time) events.push({ position, visible });
      }
    }
  }
  events.sort((a, b) => a.position - b.position);
  return events.length ? events[events.length - 1].visible : true;
}

// Unreal coordinates are centimetres, X forward, Y right, Z up. The exporter
// converts through Blender's right-handed Z-up space to glTF Y-up.
export function componentMatrix(transform) {
  const { location = {}, rotation = {}, scale = {} } = transform || {};
  const rad = THREE.MathUtils.degToRad;
  const matrix = new THREE.Matrix4().compose(
    new THREE.Vector3((location.X || 0) / 100, -(location.Y || 0) / 100, (location.Z || 0) / 100),
    new THREE.Quaternion().setFromEuler(new THREE.Euler(rad(rotation.Roll || 0), rad(-(rotation.Pitch || 0)), rad(-(rotation.Yaw || 0)), 'XYZ')),
    new THREE.Vector3(scale.X ?? 1, scale.Y ?? 1, scale.Z ?? 1),
  );
  const basis = new THREE.Matrix4().makeRotationX(-Math.PI / 2);
  return basis.clone().multiply(matrix).multiply(basis.clone().invert());
}

export function attachComponent(parent, child, component, parser) {
  let anchor = parent;
  if (component.socket) {
    const matches = [];
    const bone = component.socket_transform?.bone || component.socket;
    parent.traverse(node => {
      if (!node.isBone) return;
      const index = parser?.associations?.get(node)?.nodes;
      const name = parser ? (index === undefined ? undefined : parser.json?.nodes?.[index]?.name) : node.name;
      if (name === bone) matches.push(node);
    });
    if (matches.length !== 1) throw new Error(`Socket bone unavailable: ${bone}`);
    anchor = matches[0];
  }
  const matrix = componentMatrix(component.socket_transform).multiply(componentMatrix(component.transform));
  matrix.decompose(child.position, child.quaternion, child.scale);
  anchor.add(child);
}

export function createLinkedModels({ assets, load, dispose, report, getRoot, getParser, getAction }) {
  const declared = new Set(assets.scene?.components ?? (assets.linked_models || []).map(item => item.id));
  const items = (assets.linked_models || []).filter(item => declared.has(item.id) && !item.component.baked);
  const depth = item => { let n = 0, current = item; const seen = new Set(); while (current && !seen.has(current.id)) { seen.add(current.id); current = items.find(candidate => candidate.id === current.parent_id); n++; } return n; };
  const entries = new Map(items.sort((a, b) => depth(a) - depth(b))
    .map(item => [item.id, { item, visible: item.component.default_visible !== false, loading: false, error: '', rig: null, request: null, action: null }]));
  let closed = false;
  function emit() {
    if (!closed) report([...entries.values()].map(({ item, visible, loading, error }) => ({ id: item.id, name: item.label, visible, loading, error })));
  }
  function parentOf(entry) {
    if (entry.item.parent_id === assets.id) return { scene: getRoot(), parser: getParser?.() };
    return entries.get(entry.item.parent_id)?.rig;
  }
  function reattach() {
    for (const entry of entries.values()) if (entry.rig) entry.rig.scene.removeFromParent();
    for (const entry of entries.values()) if (entry.rig) {
      const parent = parentOf(entry);
      try {
        if (!parent?.scene) throw new Error('Parent component unavailable');
        attachComponent(parent.scene, entry.rig.scene, entry.item.component, parent.parser); entry.error = '';
      } catch (error) { entry.error = error.message || 'Component could not be attached'; }
    }
    emit();
  }
  async function ensure(entry) {
    if (entry.rig || closed) return;
    if (entry.request) return entry.request;
    entry.loading = true; entry.error = ''; emit();
    entry.request = (async () => {
      let rig, pack;
      try {
        const parent = entries.get(entry.item.parent_id);
        if (parent) { await ensure(parent); if (!parent.rig) throw new Error('Parent component unavailable'); }
        if (!entry.item.preview) throw new Error('Component preview unavailable');
        rig = await load(entry.item.preview.url);
        if (closed) return;
        let clips = rig.animations.map((clip, index) => attachAnimationCatalog(clip, rig, index));
        if (entry.item.animations && entry.item.component.bindings?.some(binding => binding.parent_clip && binding.child_clip)) {
          pack = await load(entry.item.animations.url);
          clips = bindAnimationClips(pack, rig);
        }
        if (closed) return;
        const target = parentOf(entry);
        if (!target?.scene) throw new Error('Parent component unavailable');
        prepareAnimatedMeshes(rig.scene);
        attachComponent(target.scene, rig.scene, entry.item.component, target.parser);
        rig.scene.visible = entry.visible;
        entry.rig = rig; entry.clips = clips; entry.mixer = new THREE.AnimationMixer(rig.scene);
        rig = null;
        sync();
      } catch (error) {
        if (!closed) entry.error = error.message || 'Component could not be loaded';
      } finally {
        if (pack) dispose(pack.scene);
        if (rig) dispose(rig.scene);
        entry.loading = false; entry.request = null; emit();
      }
    })();
    return entry.request;
  }
  function sync() {
    for (const entry of entries.values()) {
      if (!entry.rig) continue;
      const parent = entries.get(entry.item.parent_id);
      const parentAction = parent ? parent.action : getAction();
      entry.rig.scene.visible = entry.visible && cueVisibility(entry.item.component.cues, parentAction);
      const parentPath = packagePath(parentAction?.getClip().userData?.sourcePackage);
      const bindings = (entry.item.component.bindings || []).filter(binding => parentPath && packagePath(binding.parent_clip) === parentPath && binding.child_clip);
      const clip = bindings.length === 1 ? uniqueClip(entry.clips, bindings[0].child_clip) : undefined;
      if (entry.action?.getClip() !== clip) {
        entry.mixer.stopAllAction(); entry.action = clip ? entry.mixer.clipAction(clip).reset().play() : null;
      }
      if (entry.action && parentAction) {
        entry.action.time = clip.duration ? parentAction.time % clip.duration : 0;
        entry.mixer.update(0);
      }
    }
  }
  return {
    start() { emit(); for (const entry of entries.values()) if (entry.visible) void ensure(entry); },
    set(id, visible) { const entry = entries.get(id); if (!entry) return; entry.visible = visible; if (entry.rig) { if (visible && entry.error) reattach(); sync(); } if (visible) void ensure(entry); emit(); },
    retry(id) { const entry = entries.get(id); if (entry) void ensure(entry); },
    detach() { for (const entry of entries.values()) entry.rig?.scene.removeFromParent(); },
    reattach, sync,
    dispose() { closed = true; for (const entry of entries.values()) if (entry.rig) { entry.rig.scene.removeFromParent(); entry.mixer.stopAllAction(); entry.mixer.uncacheRoot(entry.rig.scene); dispose(entry.rig.scene); } },
  };
}
