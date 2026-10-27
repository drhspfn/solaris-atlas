import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { SpringBones } from './spring-bones.js';

const $ = id => document.getElementById(id);
const viewport = $('viewport'), status = $('status');
const parameters = new URLSearchParams(location.search);
const displayName = parameters.get('name');
if (displayName) { document.querySelector('h1').textContent = displayName; document.title = `${displayName} · Model viewer`; }
function resourceUrl(value) {
  const url = new URL(value, location.href);
  if (url.origin !== location.origin || !['http:', 'https:'].includes(url.protocol)) {
    throw new Error('Model resources must be served from the viewer origin.');
  }
  return url.href;
}
const scene = new THREE.Scene();
scene.background = new THREE.Color('#111b1d');
scene.fog = new THREE.Fog('#111b1d', 8, 24);
const camera = new THREE.PerspectiveCamera(35, 1, 0.01, 60);
function createRenderer() {
  try { return new THREE.WebGLRenderer({ antialias: true }); }
  catch (error) {
    status.textContent = 'WebGL is unavailable. Enable browser graphics acceleration and reload.';
    $('retry').hidden = false;
    throw error;
  }
}
const renderer = createRenderer();
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.15;
renderer.domElement.setAttribute('aria-label', '3D character. Use camera buttons to rotate or zoom.');
viewport.append(renderer.domElement);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.minDistance = 0.5;
controls.maxDistance = 12;
controls.maxPolarAngle = Math.PI * 0.49;
scene.add(new THREE.HemisphereLight('#e5f0ff', '#657370', 2.6));
const keyLight = new THREE.DirectionalLight('#ffffff', 2.5);
keyLight.position.set(3, 5, 4); scene.add(keyLight);
const rimLight = new THREE.DirectionalLight('#82cbcd', 1);
rimLight.position.set(-3, 2, -2); scene.add(rimLight);

const floor = new THREE.Group();
const plane = new THREE.Mesh(new THREE.PlaneGeometry(40, 40),
  new THREE.MeshStandardMaterial({ color: '#162123', roughness: 1 }));
plane.rotation.x = -Math.PI / 2; plane.position.y = -0.006; floor.add(plane);
floor.add(new THREE.GridHelper(20, 40, '#647d79', '#304345'));
const axes = new THREE.AxesHelper(0.6); axes.position.y = 0.003; floor.add(axes);
scene.add(floor);
$('floor').onchange = () => { floor.visible = $('floor').checked; $('axis-key').hidden = !floor.visible; };

let model, mixer, action, physics, applied = false, playing = !matchMedia('(prefers-reduced-motion: reduce)').matches;
let height = 1.8, clips = [], previousTime = 0;
const homeTarget = new THREE.Vector3();
function resetView() {
  controls.target.copy(homeTarget);
  camera.position.copy(homeTarget).add(new THREE.Vector3(0, height * 0.12, height * 2.05));
  controls.update();
}
$('reset').onclick = resetView;
function orbit(angle) {
  const offset = camera.position.clone().sub(controls.target);
  offset.applyAxisAngle(new THREE.Vector3(0, 1, 0), angle);
  camera.position.copy(controls.target).add(offset); controls.update();
}
$('left').onclick = () => orbit(-Math.PI / 8);
$('right').onclick = () => orbit(Math.PI / 8);
function zoom(factor) { camera.position.sub(controls.target).multiplyScalar(factor).add(controls.target); controls.update(); }
$('closer').onclick = () => zoom(0.85);
$('farther').onclick = () => zoom(1.15);

function restorePhysics() {
  if (applied) { physics.restore(); applied = false; }
}
function resetPhysics() { restorePhysics(); physics?.reset(); }
function physicsState() {
  $('physics-state').textContent = $('physics').checked
    ? `Springs on · ${physics.joints.length} bones` : 'Physics off';
  $('strength').disabled = !$('physics').checked;
}
$('physics').onchange = () => { resetPhysics(); physicsState(); };
$('strength').oninput = () => {
  $('strength-value').value = `${Math.round(Number($('strength').value) * 100)}%`;
  if (Number($('strength').value) === 0) resetPhysics();
};
function updatePlayButton() {
  $('play').textContent = playing ? 'Pause' : 'Play';
  $('play').setAttribute('aria-label', playing ? 'Pause animation' : 'Play animation');
}
$('play').onclick = () => { playing = !playing; updatePlayButton(); };
function selectClip(name) {
  resetPhysics();
  mixer.stopAllAction();
  action = mixer.clipAction(clips.find(clip => clip.name === name));
  action.reset().play(); mixer.update(0);
  $('time').max = Math.max(action.getClip().duration, 0.001);
  $('time').disabled = action.getClip().duration === 0;
  updateTime();
}
$('animations').onchange = () => selectClip($('animations').value);
$('time').oninput = () => {
  resetPhysics(); action.time = Number($('time').value); mixer.update(0); updateTime();
};
function timestamp(seconds) {
  return `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`;
}
function updateTime() {
  $('time').value = action.time;
  $('duration').value = `${timestamp(action.time)} / ${timestamp(action.getClip().duration)}`;
}
new ResizeObserver(() => {
  const { width, height } = viewport.getBoundingClientRect();
  renderer.setSize(width, height); camera.aspect = width / height; camera.updateProjectionMatrix();
}).observe(viewport);
document.addEventListener('visibilitychange', () => { previousTime = 0; resetPhysics(); });
renderer.setAnimationLoop(milliseconds => {
  const dt = previousTime ? (milliseconds - previousTime) / 1000 : 0;
  previousTime = milliseconds;
  if (mixer && playing && !document.hidden) {
    restorePhysics();
    const safeDelta = dt > 0.15 ? 0 : dt;
    mixer.update(safeDelta * Number($('speed').value));
    if ($('physics').checked) {
      if (dt > 0.15) physics.reset();
      physics.update(safeDelta, Number($('strength').value)); applied = safeDelta > 0;
    }
    updateTime();
  }
  controls.update(); renderer.render(scene, camera);
});

async function load() {
  try {
    const gltf = await new GLTFLoader().loadAsync(resourceUrl(parameters.get('model') || './character.glb'), progress => {
      status.textContent = progress.total ? `Loading model · ${Math.round(progress.loaded / progress.total * 100)}%` : 'Loading model…';
    });
    model = gltf.scene; scene.add(model);
    const bounds = new THREE.Box3().setFromObject(model);
    const center = bounds.getCenter(new THREE.Vector3());
    height = bounds.max.y - bounds.min.y;
    model.position.add(new THREE.Vector3(-center.x, -bounds.min.y, -center.z));
    model.updateMatrixWorld(true);
    homeTarget.set(0, height * 0.5, 0); resetView();
    clips = gltf.animations;
    mixer = new THREE.AnimationMixer(model);
    $('animations').replaceChildren(...clips.map(clip => new Option(
      clip.duration === 0 ? `${clip.name} · pose` : clip.name, clip.name)));
    if (clips.length) {
      $('playback').disabled = false;
      $('animations').value = clips.some(clip => clip.name === 'Stand1') ? 'Stand1' : clips[0].name;
      selectClip($('animations').value);
      // Ground the initial animated skin, not just the GLB bind-pose bounds.
      model.updateMatrixWorld(true);
      const posedBounds = new THREE.Box3().setFromObject(model, true);
      model.position.y -= posedBounds.min.y;
    }
    status.textContent = clips.length ? `${clips.length} clips · local client export` : 'Rigged model · no animations';
    $('playback').hidden = clips.length === 0;
    updatePlayButton();
    try {
      const physicsUrl = parameters.get('physics') || (parameters.has('model') ? null : './physics.json');
      if (!physicsUrl) throw new Error('No physics profile');
      const response = await fetch(resourceUrl(physicsUrl));
      if (!response.ok) throw new Error('Physics profile unavailable');
      physics = new SpringBones(model, await response.json());
      $('physics').disabled = physics.joints.length === 0;
      physicsState();
      if (physics.missing.length) $('physics-state').textContent += ` · ${physics.missing.length} roots missing`;
    } catch {
      $('physics-state').textContent = 'Physics profile unavailable · animation only';
    }
  } catch (error) {
    status.textContent = `Could not load the model: ${error.message}`;
    $('retry').hidden = false;
  }
}
$('retry').onclick = () => location.reload();
renderer.domElement.addEventListener('webglcontextlost', event => {
  event.preventDefault(); playing = false; updatePlayButton();
  status.textContent = 'Graphics context lost. Reload the preview to continue.';
  $('retry').hidden = false;
});
load();
