import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { SpringBones } from './spring-bones.js';
import { BoneCamera, cameraBones } from './camera-rig.js';
import { RootMotionLock } from './root-motion.js';
import { FacialAnimation } from './facial.js';
import { attachAnimationCatalog, loadAnimationPacks, bindAnimationPacks, disposeAnimationPacks, prepareAnimatedMeshes } from './animations.js';
import { declaredAccessories } from './accessories.js';
import { createLinkedModels } from './linked-models.js';
import { installHairStencil } from './hair-stencil.js';
import { installEyeMaterials } from './eye-material.js';

/** Dispose shared GPU resources once, including failed/obsolete model loads. */
export function disposeObject(root) {
  const resources = new Set();
  root.traverse(node => {
    if (node.geometry) resources.add(node.geometry);
    if (node.skeleton) resources.add(node.skeleton);
    for (const material of [node.material || []].flat()) {
      resources.add(material);
      for (const value of Object.values(material)) if (value?.isTexture) resources.add(value);
    }
  });
  for (const resource of resources) resource.dispose();
}

// Only asset fields authorize origins; component metadata and scene cues do not.
// The API permits at most 32 linked models. Bound traversal defensively too.
export function declaredResourceOrigins(assets, base) {
  const origins = new Set(), pending = [assets], visited = new Set();
  while (pending.length && visited.size < 33) {
    const item = pending.shift();
    if (!item || typeof item !== 'object' || visited.has(item)) continue;
    visited.add(item);
    for (const asset of [item.preview, item.download, item.animations, item.animation_manifest, item.facial, item.physics,
      ...(Array.isArray(item.animation_packs) ? item.animation_packs : [])]) {
      if (typeof asset?.url !== 'string') continue;
      try {
        const url = new URL(asset.url, base);
        if (!url.username && !url.password && ['http:', 'https:'].includes(url.protocol)
            && (new URL(base).protocol !== 'https:' || url.protocol === 'https:')) origins.add(url.origin);
      } catch { /* Invalid resource URLs are rejected when requested. */ }
    }
    if (Array.isArray(item.linked_models)) {
      for (const child of item.linked_models.slice(0, 33 - visited.size)) pending.push(child);
    }
  }
  return origins;
}

/**
 * Browser-only renderer. React owns all controls; this module owns the canvas and GPU lifecycle.
 * @param {HTMLElement} host
 * @param {import('../types').ModelAssets} assets
 * @param {boolean} preview
 * @param {import('../types').ViewerSettings} initialSettings
 * @param {(patch: Partial<import('../types').ViewerState>) => void} onState
 * @returns {import('../types').ViewerController}
 */
export function createViewer(host, assets, preview, initialSettings, onState) {
  let disposed = false, settings = initialSettings, model, gltf, mixer, action, physics, root, face;
  let clips = [], animationPack, boneList, applied = false, full = false;
  let qualityRequest, facialRequest, animationRequest, installations = Promise.resolve();
  let accessories, linked;
  const accessoryVisibility = {};
  const commandRequests = new Map();
  const loading = { quality: false, animations: false, facial: false };
  const loadErrors = { quality: '', animations: '', facial: '' };
  // Network requests run independently; changes to the live rig are serialized.
  function mutate(operation) {
    const next = installations.then(() => {
      if (disposed) throw new Error('Viewer closed');
      return operation();
    });
    installations = next.catch(() => {});
    return next;
  }
  let reportedTime = -1, reportedDuration = -1;
  let previousTime = 0, lastReport = 0, height = 1.8, width = 1.2;
  const abort = new AbortController();
  const emit = patch => { if (!disposed) onState(patch); };
  const origins = declaredResourceOrigins(assets, location.href);
  function resource(value) {
    const url = new URL(value, location.href);
    if (url.username || url.password || !['http:', 'https:'].includes(url.protocol)
      || !origins.has(url.origin) || (location.protocol === 'https:' && url.protocol !== 'https:'))
      throw new Error('Invalid model resource URL');
    return url.href;
  }
  resource(assets.preview.url);
  const scene = new THREE.Scene();
  scene.background = preview ? null : new THREE.Color('#0b1112');
  scene.fog = preview ? null : new THREE.Fog('#0b1112', 8, 24);
  const stage = new THREE.Group();
  scene.add(stage);
  const camera = new THREE.PerspectiveCamera(35, 1, 0.001, 60);
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, stencil: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  renderer.shadowMap.enabled = !preview;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.15;
  renderer.domElement.setAttribute('aria-label', '3D character. Drag to orbit; scroll to zoom.');
  host.append(renderer.domElement);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.minDistance = 0.015;
  controls.maxDistance = 12;
  controls.maxPolarAngle = Math.PI;
  controls.enabled = !preview;
  const follow = new BoneCamera(camera, controls);
  const target = new THREE.Vector3();
  scene.add(new THREE.HemisphereLight('#e5f0ff', '#657370', 2.6));
  const key = new THREE.DirectionalLight('#ffffff', 2.5);
  key.castShadow = !preview;
  key.shadow.mapSize.set(1024, 1024);
  key.shadow.normalBias = 0.005;
  key.position.set(3, 5, 4); scene.add(key);
  const rim = new THREE.DirectionalLight('#82cbcd', 1);
  rim.position.set(-3, 2, -2); scene.add(rim);
  const floor = new THREE.Group();
  const plane = new THREE.Mesh(new THREE.PlaneGeometry(40, 40),
    new THREE.MeshStandardMaterial({ color: '#162123', roughness: 1 }));
  plane.rotation.x = -Math.PI / 2; plane.position.y = -0.006;
  plane.receiveShadow = true;
  floor.add(plane, new THREE.GridHelper(20, 40, '#647d79', '#304345'), new THREE.AxesHelper(0.6));
  floor.visible = !preview && settings.floor;
  scene.add(floor);

  const manager = new THREE.LoadingManager();
  manager.setURLModifier(url => /^(blob:|data:)/.test(url) ? url : resource(url));
  const loader = new GLTFLoader(manager);
  // Three's FileLoader shares in-flight requests; completed requests use the
  // browser HTTP cache. Parsed packs belong only to this viewer and are released on close.
  async function loadGLB(url) {
    const loaded = await loader.loadAsync(resource(url), progress => {
      emit({ status: progress.total ? `Loading model · ${Math.round(progress.loaded / progress.total * 100)}%` : 'Loading model…' });
    });
    if (disposed) { disposeObject(loaded.scene); throw new Error('Viewer closed'); }
    return loaded;
  }
  async function json(url) {
    const response = await fetch(resource(url), { signal: abort.signal, credentials: 'omit' });
    if (!response.ok) throw new Error(`Model resource unavailable (${response.status})`);
    const data = await response.json();
    if (disposed) throw new Error('Viewer closed');
    return data;
  }
  function restore() { if (applied) { physics.restore(); applied = false; } }
  function updateMixer(delta) {
    accessories?.restore();
    mixer.update(delta);
    accessories?.apply();
    linked?.sync();
  }
  function resetPhysics() { restore(); physics?.reset(); }
  function reset() {
    follow.detach(); camera.up.set(0, 1, 0); controls.target.copy(target);
    const distance = Math.max(height, width / camera.aspect) / (2 * Math.tan(THREE.MathUtils.degToRad(camera.fov / 2))) * 1.15;
    camera.position.copy(target).add(new THREE.Vector3(0, height * 0.12, distance));
    controls.update();
  }
  function reportTime() {
    const expression = face?.clips.get(face.selected);
    const duration = action?.getClip().duration ?? expression?.seconds ?? 0;
    const time = action?.time ?? (duration ? face.time % duration : 0);
    if (time !== reportedTime || duration !== reportedDuration) {
      reportedTime = time; reportedDuration = duration; emit({ time, duration });
    }
  }
  function selectAnimation(name) {
    const clip = clips.find(item => item.name === name);
    if (!clip || !mixer) return;
    resetPhysics(); accessories?.restore(); mixer.stopAllAction();
    action = mixer.clipAction(clip); action.reset().play(); updateMixer(0);
    face?.update(name, 0); root?.apply();
    emit({ animation: name }); reportTime();
  }
  function installClips(next, previous) {
    clips = next;
    emit({ animations: clips.map(clip => ({ value: clip.name, label: clip.userData?.catalog?.label || clip.name,
      path: [clip.userData?.catalog?.category || 'Other animations', clip.userData?.catalog?.group,
        clip.userData?.catalog?.subgroup].filter(Boolean) })) });
    const initial = clips.find(clip => clip.name === previous?.name)?.name
      || clips.find(clip => clip.name === 'Stand1')?.name || clips[0]?.name;
    if (initial) {
      selectAnimation(initial);
      if (initial === previous?.name) {
        action.time = previous.time; updateMixer(0); face?.update(initial, action.time); reportTime();
      }
    }
  }
  function applySettings(next) {
    const old = settings; settings = next;
    floor.visible = !preview && settings.floor;
    if (root) {
      if (old.lock !== next.lock || JSON.stringify(old.axes) !== JSON.stringify(next.axes)) resetPhysics();
      root.enabled = settings.lock; root.axes = { ...settings.axes }; root.apply();
    }
    if (old.physics !== next.physics || old.strength !== next.strength) resetPhysics();
    follow.setSpace(settings.space);
    follow.setOffset(...settings.offset);
    if (settings.follow) follow.attach(boneList?.bones.get(settings.bone || boneList?.preferred), {
      space: settings.space, offset: new THREE.Vector3(...settings.offset),
    });
    else follow.detach();
  }
  async function loadPhysics() {
    physics = undefined;
    if (!assets.physics || preview) return;
    try {
      const profile = await json(assets.physics.url);
      physics = new SpringBones(model, profile);
      emit({ physicsBones: physics.joints.length, physicsStatus: `${physics.joints.length} spring bones · ${physics.missing.length} missing roots` });
    } catch (error) {
      emit({ physicsBones: 0, physicsStatus: 'Physics profile unavailable · animation only' });
    }
  }
  async function install(loaded, upgrading = false) {
    const next = preview ? [] : animationPack ? bindAnimationPacks(animationPack, loaded)
      : loaded.animations.map((clip, index) => attachAnimationCatalog(clip, loaded, index));
    const previous = { name: action?.getClip().name, time: action?.time || 0, rotation: model?.rotation.clone() };
    resetPhysics(); root?.restore(); follow.detach();
    if (mixer) { mixer.stopAllAction(); mixer.uncacheRoot(model); }
    linked?.detach();
    if (model) { stage.remove(model); disposeObject(model); }
    gltf = loaded; model = loaded.scene; stage.add(model); prepareAnimatedMeshes(model);
    installHairStencil(model);
    installEyeMaterials(model);
    if (!preview) model.traverse(node => {
      if (!node.isMesh) return;
      const materials = [node.material].flat();
      node.castShadow = materials.some(material => material && material.colorWrite && !material.transparent);
      node.receiveShadow = node.castShadow;
    });
    const declared = accessories = declaredAccessories(model, accessoryVisibility);
    for (const option of declared.options) accessoryVisibility[option.name] = option.visible;
    emit({ accessories: declared.options });
    const bounds = new THREE.Box3().setFromObject(model);
    const center = bounds.getCenter(new THREE.Vector3());
    height = bounds.max.y - bounds.min.y; width = bounds.max.x - bounds.min.x;
    model.position.add(new THREE.Vector3(-center.x, -bounds.min.y, -center.z));
    model.updateMatrixWorld(true);
    root = new RootMotionLock(stage, model);
    boneList = cameraBones(model, loaded.parser);
    emit({ bones: boneList.options, preferredBone: boneList.preferred || '', rootAvailable: Boolean(root.root) });
    target.set(0, height * 0.5, 0);
    if (!upgrading) reset();
    else if (previous.rotation) model.rotation.copy(previous.rotation);
    mixer = new THREE.AnimationMixer(model); action = undefined;
    linked?.reattach();
    installClips(next, previous);
    if (clips.length) {
      model.updateMatrixWorld(true);
      const posed = new THREE.Box3().setFromObject(model, true);
      model.position.y -= posed.min.y; root.reference.y -= posed.min.y;
    }
    applySettings(settings);
    await loadPhysics();
    emit({ ready: true, status: 'Model ready' });
  }
  function quality() {
    if (full) return Promise.resolve();
    if (qualityRequest) return qualityRequest;
    if (!assets.download) return Promise.reject(new Error('Full model is unavailable'));
    qualityRequest = (async () => {
      const loaded = await loadGLB(assets.download.url);
      try {
        await mutate(async () => {
          await install(loaded, true);
          full = true; emit({ fullQuality: true });
        });
      } catch (error) { if (loaded.scene !== model) disposeObject(loaded.scene); throw error; }
    })().finally(() => { qualityRequest = undefined; });
    return qualityRequest;
  }
  function facial() {
    if (face) return Promise.resolve();
    if (facialRequest) return facialRequest;
    if (!assets.facial) return Promise.reject(new Error('Facial animations are unavailable'));
    facialRequest = (async () => {
      // Download curves while quality is loading, but attach only to the full rig.
      const [data] = await Promise.all([json(assets.facial.url), quality()]);
      await mutate(() => {
        face = new FacialAnimation(data, model);
        emit({ facialLoaded: true, faces: [
          { value: 'auto', label: 'Follow body animation' }, { value: 'off', label: 'Off · neutral expression' },
          ...[...face.clips.values()].map(clip => ({ value: clip.name, label: clip.name })),
        ] });
      });
    })().finally(() => { facialRequest = undefined; });
    return facialRequest;
  }
  function animations() {
    if (animationPack) return Promise.resolve();
    if (animationRequest) return animationRequest;
    animationRequest = (async () => {
      const packs = await loadAnimationPacks(assets, loadGLB, disposeObject);
      try {
        await mutate(() => {
          const next = bindAnimationPacks(packs, gltf);
          installClips(next); animationPack = packs;
          linked?.enableAnimations();
          emit({ animationsLoaded: true });
        });
      } finally { disposeAnimationPacks(packs, disposeObject); }
    })().finally(() => { animationRequest = undefined; });
    return animationRequest;
  }
  async function command(name) {
    if (disposed) return;
    if (name === 'reset') return reset();
    if (name === 'focus') {
      follow.frame(boneList?.bones.get(settings.bone || boneList?.preferred), new THREE.Vector3(...settings.offset));
      return;
    }
    if (['left', 'right', 'closer', 'farther'].includes(name)) {
      const offset = camera.position.clone().sub(controls.target);
      if (name === 'left' || name === 'right') offset.applyAxisAngle(new THREE.Vector3(0, 1, 0), (name === 'left' ? -1 : 1) * Math.PI / 8);
      else offset.setLength(THREE.MathUtils.clamp(offset.length() * (name === 'closer' ? 0.85 : 1.15), controls.minDistance, controls.maxDistance));
      camera.position.copy(controls.target).add(offset); controls.update(); return;
    }
    if (!['quality', 'animations', 'facial'].includes(name)) return;
    if (commandRequests.has(name)) return commandRequests.get(name);
    loading[name] = true; loadErrors[name] = '';
    emit({ loading: { ...loading }, loadErrors: { ...loadErrors }, busy: true });
    const request = (async () => {
      try {
        await ({ quality, animations, facial })[name]();
      } catch (error) {
        loadErrors[name] = error.message || 'Could not load model resources';
      } finally {
        loading[name] = false; commandRequests.delete(name);
        emit({ loading: { ...loading }, loadErrors: { ...loadErrors },
          busy: Object.values(loading).some(Boolean) });
      }
    })();
    commandRequests.set(name, request);
    return request;
  }

  const observer = new ResizeObserver(() => {
    const { width, height } = host.getBoundingClientRect();
    if (!width || !height) return;
    renderer.setSize(width, height); camera.aspect = width / height; camera.updateProjectionMatrix();
  });
  observer.observe(host);
  const visibility = () => { previousTime = 0; resetPhysics(); };
  document.addEventListener('visibilitychange', visibility);
  const contextLost = event => {
    event.preventDefault(); renderer.setAnimationLoop(null);
    emit({ ready: false, error: 'Graphics context lost. Retry the viewer.' });
  };
  renderer.domElement.addEventListener('webglcontextlost', contextLost);
  renderer.setAnimationLoop(milliseconds => {
    const dt = previousTime ? (milliseconds - previousTime) / 1000 : 0; previousTime = milliseconds;
    if (disposed || document.hidden) return;
    const delta = dt > 0.15 ? 0 : dt;
    if (mixer) {
      restore(); root?.restore();
      if (settings.turntable && !preview) model.rotation.y += delta * 0.5;
      updateMixer(settings.playing && !preview ? delta * settings.speed : 0); root?.apply();
      if (physics && settings.physics && (settings.playing || settings.turntable) && !preview) {
        if (!delta) physics.reset(); physics.update(delta, settings.strength); applied = delta > 0;
      }
    }
    face?.update(action?.getClip().name, action?.time || 0, settings.playing ? delta * settings.speed : 0);
    if (milliseconds - lastReport > 100) { reportTime(); lastReport = milliseconds; }
    follow.update(); controls.update(); renderer.render(scene, camera);
  });
  if (!preview && (assets.linked_models?.length || assets.scene?.cues?.length)) {
    linked = createLinkedModels({ assets, load: loadGLB, dispose: disposeObject,
      report: components => emit({ components }), getRoot: () => model, getParser: () => gltf?.parser, getAction: () => action });
  }
  loadGLB(assets.preview.url).then(async loaded => {
    try { await mutate(() => install(loaded)); linked?.start(); }
    catch (error) { if (loaded.scene !== model) disposeObject(loaded.scene); throw error; }
  }).catch(error => emit({ error: error.message, ready: false }));
  return {
    settings: applySettings, command, animation: selectAnimation,
    component(id, visible) { linked?.set(id, visible); },
    accessory(name, visible) {
      if (!accessories?.set(name, visible)) return;
      accessoryVisibility[name] = visible;
      emit({ accessories: [...accessories.nodes.keys()].map(name => ({ name, visible: accessoryVisibility[name] })) });
    },
    face(name) { face?.select(name); face?.update(action?.getClip().name, action?.time || 0); emit({ face: name }); reportTime(); },
    seek(time) {
      resetPhysics();
      if (action) { action.time = THREE.MathUtils.clamp(time, 0, action.getClip().duration); updateMixer(0); root?.apply(); }
      else if (face) face.time = Math.max(0, time);
      face?.update(action?.getClip().name, action?.time || 0); reportTime();
    },
    dispose() {
      if (disposed) return;
      disposed = true; abort.abort(); observer.disconnect(); renderer.setAnimationLoop(null);
      document.removeEventListener('visibilitychange', visibility);
      renderer.domElement.removeEventListener('webglcontextlost', contextLost);
      controls.dispose(); mixer?.stopAllAction(); if (model) mixer?.uncacheRoot(model);
      linked?.dispose();
      animationPack = undefined; clips = [];
      disposeObject(scene); scene.clear(); renderer.dispose(); renderer.forceContextLoss(); renderer.domElement.remove();
    },
  };
}
