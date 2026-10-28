import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { SpringBones } from "./spring-bones.js";
import { BoneCamera, cameraBones } from "./camera-rig.js";
import { createViewerUI } from "./viewer-ui.js";
import {
  resourceUrl as validatedResourceUrl,
  animationLoadLabel,
  attachAnimationCatalog,
  bindAnimationClips,
  lazyAnimationPack,
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

let model,
  mixer,
  action,
  physics,
  applied = false,
  playing = !matchMedia("(prefers-reduced-motion: reduce)").matches;
let height = 1.8,
  modelWidth = 1.2,
  clips = [],
  previousTime = 0;
const homeTarget = new THREE.Vector3();
function resetView() {
  boneCamera.detach();
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
  $("time").max = Math.max(action.getClip().duration, 0.001);
  $("time").disabled = action.getClip().duration === 0;
  updateTime();
  ui.selectAnimation(name);
}
$("time").oninput = () => {
  if (!action) return;
  resetPhysics();
  action.time = Number($("time").value);
  mixer.update(0);
  updateTime();
};
function timestamp(seconds) {
  return `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
}
function updateTime() {
  if (!action) return;
  $("time").value = action.time;
  $("duration").value =
    `${timestamp(action.time)} / ${timestamp(action.getClip().duration)}`;
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
    const safeDelta = dt > 0.15 ? 0 : dt;
    if ($("turntable").checked) model.rotation.y += safeDelta * 0.5;
    mixer.update(playing ? safeDelta * Number($("speed").value) : 0);
    if ($("physics").checked && physics) {
      if (dt > 0.15) physics.reset();
      physics.update(safeDelta, Number($("strength").value));
      applied = safeDelta > 0;
    }
    updateTime();
  }
  boneCamera.update();
  controls.update();
  renderer.render(scene, camera);
});

async function load() {
  try {
    const gltf = await new GLTFLoader().loadAsync(
      resourceUrl(parameters.get("model") || "./character.glb"),
      (progress) => {
        status.textContent = progress.total
          ? `Loading model · ${Math.round((progress.loaded / progress.total) * 100)}%`
          : "Loading model…";
      },
    );
    model = gltf.scene;
    scene.add(model);
    const bounds = new THREE.Box3().setFromObject(model);
    const center = bounds.getCenter(new THREE.Vector3());
    height = bounds.max.y - bounds.min.y;
    modelWidth = bounds.max.x - bounds.min.x;
    model.position.add(new THREE.Vector3(-center.x, -bounds.min.y, -center.z));
    model.updateMatrixWorld(true);
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
    resetView();
    mixer = new THREE.AnimationMixer(model);
    function installClips(nextClips) {
      clips = nextClips;
      const initial = clips.some((clip) => clip.name === "Stand1")
        ? "Stand1"
        : clips[0]?.name;
      ui.setAnimations(clips, selectClip, initial);
      if (clips.length) {
        $("playback").disabled = false;
        selectClip(initial);
        // Ground the initial animated skin, not just the GLB bind-pose bounds.
        model.updateMatrixWorld(true);
        const posedBounds = new THREE.Box3().setFromObject(model, true);
        model.position.y -= posedBounds.min.y;
      }
      status.textContent = clips.length
        ? `${clips.length} animations available`
        : "Model ready";
      $("playback").hidden = clips.length === 0;
      updatePlayButton();
    }
    installClips(
      previewMode
        ? []
        : gltf.animations.map((clip, index) =>
            attachAnimationCatalog(clip, gltf, index),
          ),
    );
    const packUrl = parameters.get("animations");
    if (packUrl && !previewMode) {
      const button = $("load-animations");
      const label = animationLoadLabel(parameters.get("animation_bytes"));
      button.hidden = false;
      button.textContent = label;
      const loadPack = lazyAnimationPack(
        packUrl,
        (url) => new GLTFLoader().loadAsync(resourceUrl(url)),
        (pack) => {
          const boundClips = bindAnimationClips(pack, gltf);
          resetPhysics();
          installClips(boundClips);
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
        $("animation-status").textContent = "Loading animations…";
        try {
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
        }
      };
    }
    notifyViewer("ready");
    if (previewMode) return;
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
    status.textContent = `Could not load the model: ${error.message}`;
    notifyViewer("error", status.textContent);
    $("retry").hidden = false;
  }
}
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
