# Local character viewer

A standalone inspection tool for the source-backed GLB produced by
`wuwa_story_worker/model_bundle.py`. This does not add a public site route,
upload game assets or start production imports. Native selects are intentional
for this local tool; the maintained site's component owners are unchanged.

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
- Animation, pause, timeline and speed control the existing clips. Static clips
  are labelled as poses. Reduced motion starts playback paused.
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

## Verification

`npm test` exercises response to movement, angular bounds, restoration/reset,
changing animated rotations, several frame rates, nested roots, exclusions and
source-profile validation. The GitHub workflow runs the same checks without
game files. Actual GLB/browser verification remains a separate local check.

Manual checks:

1. Load the full model; verify 250 clips and a floor at the initial pose's feet.
2. Enable physics, play `AM_Attack01`, then switch to `Stand1` and `Skill01`.
3. Pause, disable physics, resume; verify no cumulative pose distortion.
4. Toggle the floor; orbit, zoom and reset. Seek and change playback speed.
5. Reload in a narrow viewport; controls remain reachable without horizontal
   overflow. A missing physics profile keeps animation playback available.
