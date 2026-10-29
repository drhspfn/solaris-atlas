import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { SpringBones } from "./spring-bones.js";
import { BoneCamera, cameraBones } from "./camera-rig.js";
import { RootMotionLock } from "./root-motion.js";
import { FacialAnimation } from "./facial.js";
import { createViewerUI } from "./viewer-ui.js";
import {
  resourceUrl as validatedResourceUrl,
  animationLoadLabel,
  attachAnimationCatalog,
  bindAnimationClips,
  lazyAnimationPack,
  prepareAnimatedMeshes,
} from "./animations.js";

const $ = (id) => document.getElementById(id);
const viewport = $("viewport"),
  status = $("status");
const parameters = new URLSearchParams(location.search);
const previewMode = parameters.get("mode") === "preview";
document.body.dataset.mode = previewMode ? "preview" : "viewer";
const ui = createViewerUI();
const displayName = parameters.get("name");
function notifyViewer(state, message) {
  // Public readiness only. The embedding profile validates the iframe window and origin.
  globalThis.parent?.postMessage({ type: "solaris-model-state", state, message }, "*");
}
if (displayName) {
  document.querySelector("h1").textContent = displayName;
  document.title = `${displayName} · Model viewer`;
}
function resourceUrl(value) {
  return validatedResourceUrl(value, location.href);
}
const scene = new THREE.Scene();
scene.background = previewMode ? null : new THREE.Color("#0b1112");
scene.fog = previewMode ? null : new THREE.Fog("#0b1112", 8, 24);
const camera = new THREE.PerspectiveCamera(35, 1, 0.001, 60);
$("retry").onclick = () => location.reload();
function createRenderer() {
  try {
    return new THREE.WebGLRenderer({ antialias: true, alpha: true });
  } catch (error) {
    status.textContent =
      "WebGL is unavailable. Enable browser graphics acceleration and reload.";
    notifyViewer("error", status.textContent);
    $("retry").hidden = false;
    throw error;
  }
}
const renderer = createRenderer();
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.15;
renderer.domElement.setAttribute(
  "aria-label",
  "3D character. Use camera buttons to rotate or zoom.",
);
viewport.append(renderer.domElement);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.minDistance = 0.015;
controls.maxDistance = 12;
controls.maxPolarAngle = Math.PI;
const boneCamera = new BoneCamera(camera, controls);
const motionStage = new THREE.Group();
scene.add(motionStage);
let rootMotion;
let boneList, selectedBone;
function cameraState() {
  $("camera-follow").checked = boneCamera.following;
  $("camera-state").textContent = boneCamera.following
    ? `Following ${boneList?.options.find((option) => option.value === selectedBone)?.label || "bone"}`
    : "Free camera";
}
function targetOffset() {
  const values = ["camera-x", "camera-y", "camera-z"].map((id) =>
    Number($(id).value),
  );
  return values.every(Number.isFinite)
    ? new THREE.Vector3(
        ...values.map((value) => THREE.MathUtils.clamp(value, -10, 10)),
      )
    : null;
}
function attachCamera() {
  const offset = targetOffset();
  if (offset)
    boneCamera.attach(boneList?.bones.get(selectedBone), {
      space: $("camera-space").value,
      offset,
    });
  cameraState();
}
$("camera-attach").onclick = attachCamera;
$("camera-detach").onclick = () => {
  boneCamera.detach();
  cameraState();
};
$("camera-follow").onchange = () => {
  if ($("camera-follow").checked) attachCamera();
  else {
    boneCamera.detach();
    cameraState();
  }
};
$("camera-focus").onclick = () => {
  const offset = targetOffset();
  if (offset) boneCamera.frame(boneList?.bones.get(selectedBone), offset);
};
$("camera-space").onchange = () => boneCamera.setSpace($("camera-space").value);
for (const id of ["camera-x", "camera-y", "camera-z"]) {
  $(id).oninput = () => {
    const offset = targetOffset();
    if (offset) boneCamera.setOffset(offset.x, offset.y, offset.z);
  };
}
$("fullscreen").onclick = async () => {
  try {
    if (document.fullscreenElement) await document.exitFullscreen();
    else await document.documentElement.requestFullscreen();
  } catch {
    $("camera-state").textContent =
      "Fullscreen is unavailable in this browser.";
  }
};
scene.add(new THREE.HemisphereLight("#e5f0ff", "#657370", 2.6));
const keyLight = new THREE.DirectionalLight("#ffffff", 2.5);
keyLight.position.set(3, 5, 4);
scene.add(keyLight);
const rimLight = new THREE.DirectionalLight("#82cbcd", 1);
rimLight.position.set(-3, 2, -2);
scene.add(rimLight);

const floor = new THREE.Group();
const plane = new THREE.Mesh(
  new THREE.PlaneGeometry(40, 40),
  new THREE.MeshStandardMaterial({ color: "#162123", roughness: 1 }),
);
plane.rotation.x = -Math.PI / 2;
plane.position.y = -0.006;
floor.add(plane);
floor.add(new THREE.GridHelper(20, 40, "#647d79", "#304345"));
const axes = new THREE.AxesHelper(0.6);
axes.position.y = 0.003;
floor.add(axes);
scene.add(floor);
floor.visible = !previewMode;
$("floor").onchange = () => {
  floor.visible = $("floor").checked;
  $("axis-key").hidden = !floor.visible;
};
function updateAxisLocks() {
  if (!rootMotion) return;
  resetPhysics();
  rootMotion.enabled = $("lock-position").checked;
  $("lock-axes").disabled = !rootMotion.enabled;
  for (const axis of ["x", "y", "z"]) rootMotion.axes[axis] = $("lock-" + axis).checked;
  rootMotion.apply();
}
for (const id of ["lock-position", "lock-x", "lock-y", "lock-z"])
  $(id).onchange = updateAxisLocks;

let model,
  mixer,
  action,
  physics,
  applied = false,
  playing = !matchMedia("(prefers-reduced-motion: reduce)").matches;
let currentGltf, loadedAnimationPack, facial;
let fullQuality = false, qualityRequest, facialRequest;
let height = 1.8,
  modelWidth = 1.2,
  clips = [],
  previousTime = 0;
const homeTarget = new THREE.Vector3();
function resetView() {
  boneCamera.detach();
  camera.up.set(0, 1, 0);
  cameraState();
  controls.target.copy(homeTarget);
  const distance =
    (Math.max(height, modelWidth / camera.aspect) /
      (2 * Math.tan(THREE.MathUtils.degToRad(camera.fov / 2)))) *
    1.15;
  camera.position
    .copy(homeTarget)
    .add(new THREE.Vector3(0, height * 0.12, distance));
  controls.update();
}
$("reset").onclick = resetView;
function orbit(angle) {
  const offset = camera.position.clone().sub(controls.target);
  offset.applyAxisAngle(new THREE.Vector3(0, 1, 0), angle);
  camera.position.copy(controls.target).add(offset);
  controls.update();
}
$("left").onclick = () => orbit(-Math.PI / 8);
$("right").onclick = () => orbit(Math.PI / 8);
function zoom(factor) {
  const offset = camera.position.clone().sub(controls.target);
  offset.setLength(
    THREE.MathUtils.clamp(
      offset.length() * factor,
      controls.minDistance,
      controls.maxDistance,
    ),
  );
  camera.position.copy(controls.target).add(offset);
  controls.update();
}
$("closer").onclick = () => zoom(0.85);
$("farther").onclick = () => zoom(1.15);

function restorePhysics() {
  if (applied) {
    physics.restore();
    applied = false;
  }
}
function resetPhysics() {
  restorePhysics();
  physics?.reset();
}
function physicsState() {
  $("physics-state").textContent = $("physics").checked
    ? `Springs on · ${physics.joints.length} bones`
    : "Physics off";
  $("strength").disabled = !$("physics").checked;
}
$("physics").onchange = () => {
  resetPhysics();
  physicsState();
};
$("strength").oninput = () => {
  $("strength-value").value =
    `${Math.round(Number($("strength").value) * 100)}%`;
  if (Number($("strength").value) === 0) resetPhysics();
};
function updatePlayButton() {
  $("play").textContent = playing ? "Pause" : "Play";
  $("play").setAttribute(
    "aria-label",
    playing ? "Pause animation" : "Play animation",
  );
}
$("play").onclick = () => {
  playing = !playing;
  updatePlayButton();
};
$("camera-pause").onclick = () => {
  playing = false;
  updatePlayButton();
};
function selectClip(name) {
  const clip = clips.find((clip) => clip.name === name);
  if (!clip || !mixer) return;
  resetPhysics();
  mixer.stopAllAction();
  action = mixer.clipAction(clip);
  action.reset().play();
  mixer.update(0);
  facial?.update(clip.name, action.time);
  rootMotion?.apply();
  $("time").max = Math.max(action.getClip().duration, 0.001);
  $("time").disabled = action.getClip().duration === 0;
  updateTime();
  ui.selectAnimation(name);
}
$("time").oninput = () => {
  if (!action) {
    if (facial?.clips.has(facial.selected)) {
      facial.time = Number($("time").value);
      facial.update(undefined, 0);
      updateTime();
    }
    return;
  }
  resetPhysics();
  action.time = Number($("time").value);
  mixer.update(0);
  facial?.update(action.getClip().name, action.time);
  rootMotion?.apply();
  updateTime();
};
function timestamp(seconds) {
  return `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
}
function updateTime() {
  const faceClip = facial?.clips.get(facial.selected);
  if (!action && !faceClip) return;
  const duration = action?.getClip().duration ?? faceClip.seconds;
  const time = action?.time ?? (duration ? facial.time % duration : 0);
  $("time").value = time;
  $("duration").value =
    `${timestamp(time)} / ${timestamp(duration)}`;
}
new ResizeObserver(() => {
  const { width, height } = viewport.getBoundingClientRect();
  if (!width || !height) return;
  renderer.setSize(width, height);
  camera.aspect = width / height;
  camera.updateProjectionMatrix();
}).observe(viewport);
document.addEventListener("visibilitychange", () => {
  previousTime = 0;
  resetPhysics();
});
renderer.setAnimationLoop((milliseconds) => {
  const dt = previousTime ? (milliseconds - previousTime) / 1000 : 0;
  previousTime = milliseconds;
  if (document.hidden) return;
  if (mixer && (playing || $("turntable").checked) && !document.hidden) {
    restorePhysics();
    rootMotion?.restore();
    const safeDelta = dt > 0.15 ? 0 : dt;
    if ($("turntable").checked) model.rotation.y += safeDelta * 0.5;
    mixer.update(playing ? safeDelta * Number($("speed").value) : 0);
    rootMotion?.apply();
    if ($("physics").checked && physics) {
      if (dt > 0.15) physics.reset();
      physics.update(safeDelta, Number($("strength").value));
      applied = safeDelta > 0;
    }
  }
  facial?.update(action?.getClip().name, action?.time || 0,
    playing && dt <= 0.15 ? dt * Number($("speed").value) : 0);
  updateTime();
  boneCamera.update();
  controls.update();
  renderer.render(scene, camera);
});

async function load(source, upgrading = false) {
  try {
    const gltf = await new GLTFLoader().loadAsync(
      resourceUrl(source || parameters.get("model") || "./character.glb"),
      (progress) => {
        status.textContent = progress.total
          ? `Loading model · ${Math.round((progress.loaded / progress.total) * 100)}%`
          : "Loading model…";
      },
    );
    const nextClips = previewMode ? [] : loadedAnimationPack
      ? bindAnimationClips(loadedAnimationPack, gltf)
      : gltf.animations.map((clip, index) => attachAnimationCatalog(clip, gltf, index));
    const previous = upgrading ? { name: action?.getClip().name, time: action?.time || 0,
      rotation: model.rotation.clone() } : null;
    const oldModel = model;
    resetPhysics();
    rootMotion?.restore();
    physics = undefined;
    boneCamera.detach();
    if (mixer) { mixer.stopAllAction(); mixer.uncacheRoot(model); }
    if (model) motionStage.remove(model);
    currentGltf = gltf;
    model = gltf.scene;
    motionStage.add(model);
    prepareAnimatedMeshes(model);
    const bounds = new THREE.Box3().setFromObject(model);
    const center = bounds.getCenter(new THREE.Vector3());
    height = bounds.max.y - bounds.min.y;
    modelWidth = bounds.max.x - bounds.min.x;
    model.position.add(new THREE.Vector3(-center.x, -bounds.min.y, -center.z));
    model.updateMatrixWorld(true);
    rootMotion = new RootMotionLock(motionStage, model);
    $("lock-position").disabled = !rootMotion.root;
    if (!rootMotion.root) $("lock-note").textContent = "Axis locking is unavailable for this model's skeleton.";
    boneList = cameraBones(model, gltf.parser);
    selectedBone = boneList.preferred;
    ui.setBones(boneList.options, (value) => {
      selectedBone = value;
      if (boneCamera.following) attachCamera();
    });
    if (selectedBone) ui.selectBone(selectedBone);
    for (const id of ["camera-attach", "camera-focus", "camera-follow"])
      $(id).disabled = !selectedBone;
    homeTarget.set(0, height * 0.5, 0);
    if (!upgrading) resetView();
    if (previous) model.rotation.copy(previous.rotation);
    mixer = new THREE.AnimationMixer(model);
    function installClips(nextClips) {
      clips = nextClips;
      const initial = clips.some(clip => clip.name === previous?.name) ? previous.name
        : clips.some((clip) => clip.name === "Stand1") ? "Stand1" : clips[0]?.name;
      ui.setAnimations(clips, selectClip, initial);
      if (clips.length) {
        $("playback").disabled = false;
        selectClip(initial);
        if (previous?.name === initial) { action.time = previous.time; mixer.update(0); }
        // Ground the initial animated skin, not just the GLB bind-pose bounds.
        model.updateMatrixWorld(true);
        const posedBounds = new THREE.Box3().setFromObject(model, true);
        model.position.y -= posedBounds.min.y;
        rootMotion.reference.y -= posedBounds.min.y;
        rootMotion.apply();
        updateTime();
      }
      status.textContent = clips.length
        ? `${clips.length} animations available`
        : "Model ready";
      $("playback").hidden = clips.length === 0;
      updatePlayButton();
    }
    installClips(nextClips);
    updateAxisLocks();
    if (oldModel && oldModel !== model) {
      // Release the compact rig only after the replacement has loaded successfully.
      const textures = new Set(), materials = new Set(), geometries = new Set();
      oldModel.traverse(node => {
        if (node.geometry) geometries.add(node.geometry);
        for (const material of node.material ? [node.material].flat() : []) {
          materials.add(material);
          for (const value of Object.values(material)) if (value?.isTexture) textures.add(value);
        }
        node.skeleton?.dispose();
      });
      for (const item of [...textures, ...materials, ...geometries]) item.dispose();
    }
    const packUrl = parameters.get("animations");
    if (packUrl && !previewMode && !loadedAnimationPack) {
      const button = $("load-animations");
      const label = parameters.get("facial") && !fullQuality
        ? assetLabel("Load animations, face + full model", Number(parameters.get("animation_bytes"))
          + Number(parameters.get("model_bytes")) + Number(parameters.get("facial_bytes")))
        : animationLoadLabel(parameters.get("animation_bytes"));
      button.hidden = false;
      button.textContent = label;
      const loadPack = lazyAnimationPack(
        packUrl,
        (url) => new GLTFLoader().loadAsync(resourceUrl(url)),
        (pack) => {
          const boundClips = bindAnimationClips(pack, currentGltf);
          resetPhysics();
          installClips(boundClips);
          loadedAnimationPack = pack;
          $("turntable").checked = false;
          $("turntable").closest("label").hidden = true;
        },
      );
      button.onclick = async () => {
        if (button.disabled) return;
        button.disabled = true;
        button.setAttribute("aria-busy", "true");
        button.textContent = "Loading animations…";
        $("playback").disabled = true;
        $("load-quality").disabled = true;
        $("load-facial").disabled = true;
        $("animation-status").textContent = "Loading animations…";
        try {
          if (parameters.get("facial")) await loadFacial();
          await loadPack();
          button.hidden = true;
          $("animation-status").textContent =
            `${clips.length} animations loaded`;
        } catch (error) {
          button.textContent = label.replace("Load", "Retry loading");
          $("animation-status").textContent =
            `Could not load animations: ${error.message}`;
        } finally {
          button.disabled = false;
          button.removeAttribute("aria-busy");
          $("playback").disabled = clips.length === 0;
          $("load-quality").disabled = false;
          $("load-facial").disabled = false;
        }
      };
    }
    notifyViewer("ready");
    if (previewMode) return;
    if (!upgrading) qualityButton.disabled = facialButton.disabled = false;
    try {
      const physicsUrl =
        parameters.get("physics") ||
        (parameters.has("model") ? null : "./physics.json");
      if (!physicsUrl) throw new Error("No physics profile");
      const response = await fetch(resourceUrl(physicsUrl));
      if (!response.ok) throw new Error("Physics profile unavailable");
      physics = new SpringBones(model, await response.json());
      $("physics").disabled = physics.joints.length === 0;
      physicsState();
      if (physics.missing.length)
        $("physics-state").textContent +=
          ` · ${physics.missing.length} roots missing`;
    } catch {
      $("physics-state").textContent =
        "Physics profile unavailable · animation only";
    }
  } catch (error) {
    if (upgrading) throw error;
    status.textContent = `Could not load the model: ${error.message}`;
    notifyViewer("error", status.textContent);
    $("retry").hidden = false;
  }
}
function assetLabel(label, bytes) {
  const size = Number(bytes);
  return Number.isFinite(size) && size > 0 ? `${label} (~${(size / 1024 ** 2).toFixed(1)} MiB)` : label;
}
const fullModelUrl = parameters.get("full_model");
const qualityButton = $("load-quality"), facialButton = $("load-facial");
qualityButton.disabled = facialButton.disabled = true;
const qualityLabel = assetLabel("Load full quality", parameters.get("model_bytes"));
qualityButton.hidden = previewMode || !fullModelUrl;
qualityButton.textContent = qualityLabel;
async function loadFullQuality() {
  if (fullQuality) return;
  if (!fullModelUrl) throw new Error("Full model is not available");
  if (!qualityRequest) qualityRequest = load(fullModelUrl, true).then(() => {
    fullQuality = true;
    qualityButton.hidden = true;
    $("model-quality-status").textContent = "Full quality · original textures and facial morphs";
  }).finally(() => { qualityRequest = undefined; });
  return qualityRequest;
}
qualityButton.onclick = async () => {
  if (qualityButton.disabled) return;
  qualityButton.disabled = facialButton.disabled = true;
  $("load-animations").disabled = true;
  qualityButton.textContent = "Loading full model…";
  try { await loadFullQuality(); }
  catch (error) {
    qualityButton.textContent = qualityLabel.replace("Load", "Retry loading");
    $("model-quality-status").textContent = error.message;
    status.textContent = "Compact model retained";
  } finally {
    qualityButton.disabled = facialButton.disabled = false;
    $("load-animations").disabled = false;
  }
};
facialButton.hidden = previewMode || !parameters.get("facial");
const facialLabel = assetLabel("Load facial animations + full model",
  Number(parameters.get("facial_bytes")) + Number(parameters.get("model_bytes")));
facialButton.textContent = facialLabel;
async function loadFacial() {
  if (facial) return;
  if (facialRequest) return facialRequest;
  facialRequest = (async () => {
    await loadFullQuality();
    const response = await fetch(resourceUrl(parameters.get("facial")));
    if (!response.ok) throw new Error("Facial animation pack unavailable");
    const next = new FacialAnimation(await response.json(), model);
    facial = next;
    ui.setFacial([...facial.clips.values()], name => {
      facial.select(name);
      facial.update(action?.getClip().name, action?.time || 0);
      if (!action) {
        const clip = facial.clips.get(name);
        $("playback").hidden = !clip;
        $("playback").disabled = !clip;
        $("time").max = clip?.seconds || 0.001;
        $("time").disabled = !clip?.seconds;
        updateTime();
      }
      $("facial-mode").textContent = name === "auto" ? "Face · follows body animation" : name === "off" ? "Face · neutral expression" : `Face · ${name}`;
      $("facial-status").textContent = name === "auto" ? "Follows matching body clips" : name === "off" ? "Neutral expression" : "Independent facial animation";
    });
    $("facial-controls").hidden = false;
    $("facial-status").textContent = `${facial.clips.size} facial animations loaded · follows matching body clips`;
    facialButton.hidden = true;
  })().finally(() => { facialRequest = undefined; });
  return facialRequest;
}
facialButton.onclick = async () => {
  if (facialButton.disabled) return;
  qualityButton.disabled = facialButton.disabled = true;
  $("load-animations").disabled = true;
  facialButton.textContent = "Loading facial animations…";
  try {
    await loadFacial();
  } catch (error) {
    facialButton.textContent = facialLabel.replace("Load", "Retry loading");
    $("model-quality-status").textContent = error.message;
  } finally {
    qualityButton.disabled = facialButton.disabled = false;
    $("load-animations").disabled = false;
  }
};
$("retry").onclick = () => location.reload();
renderer.domElement.addEventListener("webglcontextlost", (event) => {
  event.preventDefault();
  playing = false;
  updatePlayButton();
  status.textContent = "Graphics context lost. Reload the preview to continue.";
  notifyViewer("error", status.textContent);
  $("retry").hidden = false;
});
load();
