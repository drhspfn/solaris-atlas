import * as THREE from 'three';
import { attachAnimationCatalog, loadAnimationPacks, bindAnimationPacks, disposeAnimationPacks, prepareAnimatedMeshes } from './animations.js';

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

function cueWindows(cue, parentAction) {
  const source = packagePath(parentAction?.getClip().userData?.sourcePackage);
  if (!source || !Number.isFinite(cue.start)) return [];
  const finish = Number.isFinite(cue.end) ? cue.end : cue.start + (cue.duration || 0);
  if (packagePath(cue.source_clip) === source) return [{ start: cue.start, end: finish, rate: 1 }];
  if (packagePath(cue.montage) !== source || !(cue.play_rate > 0) ||
      !Number.isFinite(cue.segment_start) || !Number.isFinite(cue.source_start) || !Number.isFinite(cue.source_end) ||
      cue.source_end <= cue.source_start || !Number.isInteger(cue.loop_count) || cue.loop_count < 1 || cue.loop_count > 1000) return [];
  const start = Math.max(cue.start, cue.source_start), end = Math.min(finish, cue.source_end);
  if (end < start) return [];
  return Array.from({ length: cue.loop_count }, (_, i) => {
    const base = cue.segment_start + i * (cue.source_end - cue.source_start) / cue.play_rate;
    return { start: base + (start - cue.source_start) / cue.play_rate,
      end: base + (end - cue.source_start) / cue.play_rate, rate: cue.play_rate };
  });
}

function cueVisibility(cues, parentAction, initial) {
  const events = [];
  for (const cue of cues || []) {
    if (cue.kind !== 'TsAnimNotifyStateSubMeshControl_C' || !cue.defaults) continue;
    const defaults = cue.defaults;
    for (const window of cueWindows(cue, parentAction)) {
      for (const [time, delay, visible] of [[window.start, defaults.begin_delay, defaults.begin_visible ?? true], [window.end, defaults.end_delay, defaults.end_visible ?? true]]) {
        if (typeof visible !== 'boolean') continue;
        const position = time + (Number.isFinite(delay) ? delay : 0);
        if (position <= parentAction.time) events.push({ position, visible });
      }
    }
  }
  events.sort((a, b) => a.position - b.position);
  return events.length ? events[events.length - 1].visible : initial;
}

function activeCue(cues, action, kind) {
  const matches = [];
  for (const cue of cues || []) if (cue.kind?.includes(kind)) {
    for (const window of cueWindows(cue, action)) if (action.time >= window.start && action.time < window.end)
      matches.push({ cue, ...window });
  }
  return matches.sort((a, b) => b.start - a.start)[0];
}

// Unreal coordinates are centimetres, X forward, Y right, Z up. The exporter
// converts through Blender's right-handed Z-up space to glTF Y-up.
export function componentMatrix(transform) {
  const location = transform?.location || {}, rotation = transform?.rotation || {}, scale = transform?.scale || {};
  const rad = THREE.MathUtils.degToRad;
  const matrix = new THREE.Matrix4().compose(
    new THREE.Vector3((location.X || 0) / 100, -(location.Y || 0) / 100, (location.Z || 0) / 100),
    Number.isFinite(rotation.W)
      ? new THREE.Quaternion(-(rotation.X || 0), rotation.Y || 0, -(rotation.Z || 0), rotation.W).normalize()
      : new THREE.Quaternion().setFromEuler(new THREE.Euler(rad(rotation.Roll || 0), rad(-(rotation.Pitch || 0)), rad(-(rotation.Yaw || 0)), 'XYZ')),
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
  // Exported bones use the converter's Z-up basis; a child glTF scene already
  // contains its own basis conversion, so undo the parent's before attaching it.
  if (anchor.isBone) matrix.premultiply(new THREE.Matrix4().makeRotationX(Math.PI / 2));
  matrix.decompose(child.position, child.quaternion, child.scale);
  anchor.add(child);
}

export function createLinkedModels({ assets, load, dispose, report, getRoot, getParser, getAction }) {
  const declared = new Set(assets.scene?.components ?? (assets.linked_models || []).map(item => item.id));
  const items = (assets.linked_models || []).filter(item => declared.has(item.id) && !item.component.baked && !item.component.standalone_only);
  const depth = item => { let n = 0, current = item; const seen = new Set(); while (current && !seen.has(current.id)) { seen.add(current.id); current = items.find(candidate => candidate.id === current.parent_id); n++; } return n; };
  const entries = new Map(items.sort((a, b) => depth(a) - depth(b))
    .map(item => [item.id, { item, visible: item.component.default_visible !== false, override: null, loading: false, error: '', rig: null, request: null, action: null }]));
  let closed = false, animationsEnabled = false, rootModel, rootMeshes = new Map(), reported = false;
  function emit() {
    if (closed) return;
    let changed = !reported;
    for (const entry of entries.values()) {
      const previous = entry.reported;
      if (!previous || previous.visible !== entry.visible || previous.override !== entry.override ||
          previous.loading !== entry.loading || previous.error !== entry.error) { changed = true; break; }
    }
    if (!changed) return;
    const state = [];
    for (const entry of entries.values()) {
      const { item, visible, override, loading, error } = entry;
      entry.reported = { visible, override, loading, error };
      state.push({ id: item.id, name: item.label, visible, override, loading, error });
    }
    reported = true; report(state);
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
        attachComponent(parent.scene, entry.rig.scene, entry.item.component, parent.parser); entry.error = ''; entry.slot = undefined;
      } catch (error) { entry.error = error.message || 'Component could not be attached'; }
    }
    emit();
  }
  async function ensurePacks(entry) {
    if (closed || !animationsEnabled || !entry.rig || entry.packsLoaded || entry.packRequest ||
        entry.item.animation_packs == null || !entry.item.component.bindings?.some(binding => binding.parent_clip && binding.child_clip)) return;
    entry.loading = true; entry.error = ''; emit();
    entry.packRequest = (async () => {
      let packs;
      try {
        packs = await loadAnimationPacks(entry.item, load, dispose);
        if (closed) return;
        const clips = bindAnimationPacks(packs, entry.rig);
        entry.mixer.stopAllAction(); entry.action = null; entry.clips = clips; entry.packsLoaded = true;
        sync();
      } catch (error) {
        if (!closed) entry.error = error.message || 'Component animations could not be loaded';
      } finally {
        if (packs) disposeAnimationPacks(packs, dispose);
        entry.loading = false; entry.packRequest = null; emit();
      }
    })();
    return entry.packRequest;
  }
  async function ensure(entry) {
    if (entry.rig || closed) return;
    if (entry.request) return entry.request;
    entry.loading = true; entry.error = ''; emit();
    entry.request = (async () => {
      let rig, packs;
      try {
        const parent = entries.get(entry.item.parent_id);
        if (parent) { await ensure(parent); if (!parent.rig) throw new Error('Parent component unavailable'); }
        if (!entry.item.preview) throw new Error('Component preview unavailable');
        rig = await load(entry.item.preview.url);
        if (closed) return;
        let clips = rig.animations.map((clip, index) => attachAnimationCatalog(clip, rig, index));
        if (entry.item.animation_packs == null && entry.item.animations && entry.item.component.bindings?.some(binding => binding.parent_clip && binding.child_clip)) {
          packs = await loadAnimationPacks(entry.item, load, dispose);
          if (closed) return;
          clips = bindAnimationPacks(packs, rig);
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
        if (packs) disposeAnimationPacks(packs, dispose);
        if (rig) dispose(rig.scene);
        entry.loading = false; entry.request = null; emit();
        if (entry.rig) void ensurePacks(entry);
      }
    })();
    return entry.request;
  }
  function rootVisibility(action) {
    const model = getRoot();
    if (rootModel !== model) {
      for (const [mesh, mask] of rootMeshes) mesh.layers.mask = mask;
      rootModel = model; rootMeshes = new Map();
      const linkedRoots = new Set([...entries.values()].map(entry => entry.rig?.scene).filter(Boolean));
      function collect(node) {
        if (linkedRoots.has(node)) return;
        if (node.userData?.accessory_name && !node.isBone) return;
        if (node.isMesh) rootMeshes.set(node, node.layers.mask);
        for (const child of node.children) collect(child);
      }
      if (model) collect(model);
    }
    const cues = [...(assets.scene?.cues || []), ...items.flatMap(item => item.component.cues || [])];
    const events = [];
    for (const cue of cues) {
      if (cue.defaults?.target !== 'notifying_mesh' || !cue.kind?.includes('HideMesh')) continue;
      const windows = cueWindows(cue, action);
      for (const window of windows) {
        if (action.time < window.start) continue;
        if (!cue.kind.includes('State') || action.time < window.end)
          events.push({ start: window.start, hidden: cue.defaults.hide !== false, children: cue.defaults.hide_children !== false });
      }
    }
    events.sort((a, b) => b.start - a.start);
    const hidden = events[0]?.hidden || false, hideChildren = events[0]?.children || false;
    for (const [mesh, mask] of rootMeshes) mesh.layers.mask = hidden ? 0 : mask;
    return hidden && hideChildren;
  }
  function sync() {
    if (closed) return;
    const hideChildren = rootVisibility(getAction());
    for (const entry of entries.values()) {
      const parent = entries.get(entry.item.parent_id);
      const parentAction = parent ? parent.action : getAction();
      const effect = entry.item.component.kind === 'effect';
      const effectCue = effect && activeCue(entry.item.component.cues, parentAction, 'Effect');
      const automatic = effect ? Boolean(effectCue) : cueVisibility(entry.item.component.cues, parentAction, entry.item.component.default_visible !== false);
      entry.visible = !hideChildren && (effect ? automatic && entry.override !== false : entry.override ?? automatic);
      const parentPath = packagePath(parentAction?.getClip().userData?.sourcePackage);
      const bindingPath = effectCue ? packagePath(effectCue.cue.source_clip) : parentPath;
      const bindings = (entry.item.component.bindings || []).filter(binding => bindingPath && packagePath(binding.parent_clip) === bindingPath && binding.child_clip);
      if (!entry.rig) { if ((entry.visible || (!effect && bindings.length === 1 && entry.override !== false)) && !entry.error) void ensure(entry); continue; }
      entry.rig.scene.visible = entry.visible;
      const slotCue = activeCue(entry.item.component.cues, parentAction, 'ChangeSlot');
      const slot = slotCue?.cue;
      if (entry.slot !== slot) {
        const target = parentOf(entry);
        try {
          const raw = slot?.properties?.SlotTransform || {};
          const transform = { location: raw.Translation || raw.Location || raw.location || {},
            rotation: raw.Rotation || raw.rotation || {}, scale: raw.Scale3D || raw.Scale || raw.scale || {} };
          const component = slot ? { socket: slot.properties?.SwitchToSlotName, socket_transform: slot.socket_transform, transform } : entry.item.component;
          if (!target?.scene) throw new Error('Parent component unavailable');
          attachComponent(target.scene, entry.rig.scene, component, target.parser);
          entry.slot = slot; entry.error = '';
        } catch (error) { entry.error = error.message || 'Component could not be attached'; }
      }
      const binding = bindings.length === 1 ? bindings[0] : undefined;
      const clip = binding && (!effect || effectCue) ? uniqueClip(entry.clips, binding.child_clip) : undefined;
      if (entry.action?.getClip() !== clip) {
        entry.mixer.stopAllAction(); entry.action = clip ? entry.mixer.clipAction(clip).reset().play() : null;
      }
      if (entry.action && parentAction) {
        const authoredRate = effectCue?.cue?.properties?.PlayRate ?? entry.item.component.source?.properties?.PlayRate ?? binding.source?.play_rate;
        const rate = Number.isFinite(authoredRate) && authoredRate >= 0 ? authoredRate : 1;
        const time = effectCue ? Math.max(0, (parentAction.time - effectCue.start) * effectCue.rate * rate) : parentAction.time;
        entry.action.time = clip.duration ? (effect && binding.source?.looping === false ? Math.min(time, clip.duration) : time % clip.duration) : 0;
        if (effect && binding.source?.looping === false) { entry.action.setLoop(THREE.LoopOnce, 1); entry.action.clampWhenFinished = true; }
        entry.mixer.update(0);
      }
    }
    emit();
  }
  return {
    start() { sync(); },
    enableAnimations() { animationsEnabled = true; for (const entry of entries.values()) void ensurePacks(entry); },
    set(id, visible) { const entry = entries.get(id); if (!entry) return; entry.override = visible; if (visible && entry.error) { entry.error = ''; if (entry.rig) { reattach(); void ensurePacks(entry); } } sync(); },
    retry(id) { const entry = entries.get(id); if (entry) { if (entry.rig) void ensurePacks(entry); else void ensure(entry); } },
    detach() { for (const entry of entries.values()) entry.rig?.scene.removeFromParent(); },
    reattach, sync,
    dispose() { closed = true; for (const [mesh, mask] of rootMeshes) mesh.layers.mask = mask; for (const entry of entries.values()) if (entry.rig) { entry.rig.scene.removeFromParent(); entry.mixer.stopAllAction(); entry.mixer.uncacheRoot(entry.rig.scene); dispose(entry.rig.scene); } },
  };
}
