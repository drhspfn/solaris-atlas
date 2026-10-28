# Local character viewer

A standalone inspection tool for the source-backed GLB produced by
`wuwa_story_worker/model_bundle.py`. This does not add a public site route,
upload game assets or start production imports. The same static runtime is staged
into the maintained frontend. Its authored listboxes and settings panels adapt
the site's controls without mounting another application inside the iframe.

```powershell
cd E:/Projects/solaris-atlas/tools/model-viewer
npm ci --ignore-scripts --no-audit --no-fund
npm test
npm run stage -- --model E:/Caches/solaris-model-lab/shorekeeper/full-bundle/character.glb --blueprint E:/Caches/solaris-model-lab/shorekeeper/source-json/Client/Content/Aki/Character/Role/FemaleM/Shouanren/R2T1ShouanrenMd10011/ABP_R2T1ShouanrenMd10011_PC.json --output E:/Caches/solaris-model-lab/shorekeeper/preview
python -m http.server 8807 --bind 127.0.0.1 --directory E:/Caches/solaris-model-lab/shorekeeper/preview
```

Open <http://127.0.0.1:8807/>. If the preview server is already running, stage
the files and reload its tab instead of starting another server. All Three.js
dependencies are pinned and copied locally with their license; no CDN is used.
Serve only the generated preview folder, never the game, keys or source logs.

## Controls and physics

- Floor & grid toggles a ground plane, a 0.5 m grid and XYZ axes. Reset view,
  orbit and zoom buttons supplement pointer/touch camera controls.
- Rotate model enables an optional turntable, so a rig without animation clips
  can move and drive secondary springs. A stationary rig has no artificial jiggle.
- Skinned parts remain renderable near viewport edges: their initial bounding
  spheres become stale as bones and springs move. The single-character viewer
  disables frustum culling only for skinned meshes instead of recomputing every
  deformed vertex each frame. Static scene meshes retain normal culling.
- Animation, pause, timeline and speed control the existing clips. Static clips
  are labelled as poses. Reduced motion starts playback paused.
- Scene settings offer **Lock position**, off initially. X/Z are preselected to
  hold the rig at the floor origin while retaining vertical motion; Y optionally
  holds its original height. Compensation uses the single shared skeleton root
  in viewer/world axes, before springs and bone-follow camera updates. Rotation,
  child poses and downloadable GLBs remain unchanged. Turning the lock off restores
  the current authored position, including after seeking or changing clips.
- The animation picker retains the selected category/subgroup when reopened.
  Previous/next buttons beside the current clip follow the loaded clip order and
  stop at either end. Changing clips with arrows opens its own subgroup on the
  next menu visit. Replacing the animation library clears obsolete menu paths.
- Secondary physics starts **off**; Strength mixes its rotations over the
  animated pose. Turning it off restores animation rotations. Clip changes,
  seeking, hidden tabs, long frames and large anchor jumps reset spring state.
- `stage.mjs` reads exact root groups, exclusions, damping, stiffness, dummy
  lengths and angular limits from an exported KawaiiPhysics blueprint. It
  records the source filename and SHA256 in `physics.json`; missing roots are
  reported in the viewer. The Shorekeeper PC profile has 11 groups / 53 roots,
  matching 93 bones in the current 250-joint GLB, including chest, hair and cloth.
- These are **animation-relative browser springs**, not Kuro's solver. Source
  damping/stiffness are mapped to bounded spring rates; capsule/planar collisions,
  wind, curves, binding constraints and cloth simulation are not implemented.
  Clothing already animated in the clip keeps that motion with a small extra
  offset. Lower Strength or switch physics off if a clip intersects the body.
  No artificial periodic jiggle is added to stationary poses.

The GLB is unchanged. `physics.json` and viewer code add only a small amount of
metadata/runtime code; no additional animation frames are baked or stored.
For published bundles the viewer accepts `model`, optional `physics`, and `name`
URL parameters. Resources must use HTTP(S) on the viewer origin;
credentials and HTTPS downgrades are rejected. `--assets-only` stages
the standalone runtime without reading or publishing any game files.

## Optional animation pack

Pass `animations=<URL>` and optionally `animation_bytes=<positive integer>`
alongside the existing parameters (URL-encode each parameter value). For example:

```text
?model=/models/preview.glb&physics=/models/physics.json&animations=/models/animations.glb&animation_bytes=10485760
```

The viewer shows **Load animations (~10.0 MiB)** for that size hint, or
**Load animations** when the size is absent/invalid. The hint does not trigger a
HEAD request. No animation-pack request occurs until the button is clicked.
The pack must be a self-contained GLB containing a bone hierarchy and clips,
without meshes, materials or textures, with the same authored bone names as the
preview. Its hierarchy is never added to the visible scene. Clips are matched
by original glTF node names and rebound to existing preview bone UUIDs, avoiding
loader name suffix collisions. Missing/ambiguous bone targets and empty packs
fail visibly and allow retry without replacing the preview or its physics.

While loading, the load button and playback controls are disabled. On success,
playback controls appear, `Stand1` is selected when present (otherwise the first
clip), and the optional turntable stops and is hidden. Reduced motion still
starts playback paused. Physics settings remain unchanged. Failed downloads
offer an explicit retry; there is no automatic retry or background download.
Embedded clips in local full GLBs continue to work without an animation URL.

## Verification

### Model studio

`mode=preview` renders a transparent, static rig without chrome, floor, physics or
animation downloads. The profile mounts this lightweight mode near the viewport
and opens the complete viewer in a native dialog on click. Deployment publishes a
complete immutable runtime then switches the revalidated CDN entry point
`model-viewer/current/index.html`. This preserves same-origin GLB loading and
updates older published bundles without reimporting them.

The animation browser drills into category/group/subgroup menus with Back and an
optional search across authored names. New imports store these deterministic labels in animation
`extras.solaris_atlas`; legacy packs use the matching fallback rules. Unknown names
stay in Other. No clip name, bone binding or animation data is changed.

Settings contains Scene, Physics and Camera. Camera supports a 0.015 m minimum
orbit distance and a 0.001 m near plane, plus following any actual exported bone.
Pause the animation, choose a bone, frame it, adjust target offsets in metres and
attach, then resume playback. World mode follows translation; bone-relative mode
also follows orientation. Pointer orbit/pan adjustments remain usable while
attached. Detach preserves the view, and Reset view returns to the whole model.
If no nose bone exists, select Head and adjust offsets instead of inventing a joint.

`npm test` exercises response to movement, angular bounds, restoration/reset,
changing animated rotations, several frame rates, nested roots, exclusions and
source-profile validation. Animation checks execute the viewer orchestration
with browser/rendering boundaries stubbed, assert zero initial pack requests,
retry/busy states, URL validation, embedded clips, default selection and physics
preservation, and verify target rebinding with a real Three.js mixer.
The GitHub workflow runs the same checks without
game files. Actual GLB/browser verification remains a separate local check.

Manual checks:

1. Load the full model; verify 250 clips and a floor at the initial pose's feet.
2. Enable physics, play `AM_Attack01`, then switch to `Stand1` and `Skill01`.
3. Pause, disable physics, resume; verify no cumulative pose distortion.
4. Toggle the floor; orbit, zoom and reset. Seek and change playback speed.
5. Reload in a narrow viewport; controls remain reachable without horizontal
   overflow. A missing physics profile keeps animation playback available.
6. Open a rig-only preview with `animations` and `animation_bytes`. In browser
   Network, verify no pack request before clicking **Load animations**. Enable
   physics/turntable, click once, and verify one pack request, `Stand1` playback,
   restored playback controls, stopped/hidden turntable and unchanged physics.
7. Block the pack URL, click to load, verify the error and enabled retry button,
   unblock it and retry. Repeat with reduced motion and at a narrow viewport.
