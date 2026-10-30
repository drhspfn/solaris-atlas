# Independent illustrated-scene playback

The worker converts a verified subset of 4.1 binary skeletal data into
`solaris.skeletal-scene.v1`. The browser evaluates this neutral scene through
the existing Three.js WebGL renderer. No official Spine Runtime, editor executable
or editor export is shipped. No new browser runtime dependency is needed.

## Source provenance

The adapter uses the public [binary format](https://esotericsoftware.com/spine-binary-format),
[JSON format](https://esotericsoftware.com/spine-json-format) and
[atlas format](https://esotericsoftware.com/spine-atlas-format) documentation,
and independently authored MIT-licensed [Spinal](https://github.com/slowchop/spinal)
at revision `807014a64bf512c77a121c61b7f11886147bd5f2`.
The 2022 prototype's `binary.rs`, `binary/bone.rs`, `binary/skin.rs` and
`binary/animation.rs` informed the binary layout; current `spinal/src/draw.rs`
informed atlas UV mapping. The incomplete prototype was corrected and extended
using source bytes, full-consumption checks and self-authored fixtures.
Its current 4.3 JSON renderer is not used: it does not cover the observed 4.1
deformation/additive/multiply profile.

This is an independent implementation, not a claim of a formally certified
clean-room process. Earlier investigation read official public TypeScript API
declarations; no official implementation source was used by this adapter or
pose evaluator. No official runtime was used as a test oracle. The pose evaluator
was authored separately from public data structures and self-authored fixtures.
MIT copyright and permission notices are preserved in `THIRD_PARTY_NOTICES.md`,
the worker adapter and `/licenses/spinal-MIT.txt`. Game artwork retains KURO's
rights; the software license does not grant rights to that content.

## Verified profile

Supported: normal bone hierarchies; translate/rotate/scale/shear channels;
attachment selection and slot RGBA; weighted and unweighted mesh geometry;
sparse mesh deformation; linear, stepped and Bezier curves; ordered slots;
normal, additive, multiply and screen blending; trimmed/rotated atlas regions;
exact prefab/animation binding; authored looping and Rover variants.

The converter rejects other binary versions, IK/transform/path constraints,
non-normal bone inheritance, skin bones, region/linked/clipping attachments,
sequences, animated draw order, event tracks and two-color tint. It never silently
replaces these features with a guessed scene. Complete source bundles are still
stored; the job reports a partial result with per-prefab diagnostics.
Additional profiles need fixture coverage before support is expanded.

The adapter validates bounded reads, finite numbers, string indices, parent order,
mesh indices, atlas bounds and trims, timeline inventory and exact EOF. The player
bounds downloads and decoded-texture budgets. Source bounds remain intact.
A viewport is narrowed only when four static, opaque black, axis-aligned border
meshes establish a geometric aperture; asset names are irrelevant.

## Rollout

`STORY_SPINE_PLAYBACK_ENABLED` defaults to `false` in API, workers and production
environment generation. Archive-only imports do not publish browser components
or become partial solely because a renderer feature is unsupported. The API does
not expose playback data while disabled, including previously generated metadata.
Set the shared flag to `true` only for an explicit playback rollout.

## Storage and repair

Archive schema 2 retains original packages, skeleton/atlas/textures, dependencies,
source identities and hashes. Playback JSON and PNG files are content-addressed
storage objects. `story_spine_part` references and `spine_open_component` variants
retain archive provenance. Public responses contain playback URLs, not internal
object inventories. Repeated publication updates existing references.

Illustration job schema 3 / Spine recipe 1 upgrade older archive-only results.
Reuse checks verify the bundle AND each playback component; missing parts remain
retryable. Existing owner locks, workspace cleanup and deduplication are reused.
After deployment, use scoped repair in `quest-runtime-blocks.md`; a full patch
import is unnecessary. No database migration is required. Older readers can ignore
the extra playback metadata.

## Browser lifecycle and verification

The WebGL module and textures load only while the illustration is visible.
Leaving aborts downloads and releases textures, geometry and context. Returning
creates a fresh context and resumes the cursor. Hidden tabs pause rendering.
Reduced-motion users start paused. Load/context failures offer retry.
The browser does not download the source ZIP to play a scene.

Self-authored Python fixtures cover binary boundaries, rejection, timelines,
weighted meshes, atlas validation, prefab binding, conversion, publication reuse
and missing-object repair. Node tests cover interpolation, deformation and atlas
coordinates. SSR tests preserve the illustrated reader.

A local, uncommitted game sample (resource 850007, version 4.1.23) consumed all
98,104 source bytes: 87 bones, 122 mesh slots, 223 timelines including 4 deform
tracks, and a ten-second idle animation. A production-build Chromium test renders
its actual atlas, checks changed pixels during playback, stable pixels while
paused, mobile overflow and context recreation after scrolling away and back.
Proprietary fixtures and screenshots are not committed.
