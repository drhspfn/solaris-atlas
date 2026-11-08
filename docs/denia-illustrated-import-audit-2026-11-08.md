# Illustrated story import audit — 2026-11-08

## Confirmed production data

The Denia flow `flow_state:剧情_3_3_达尼亚洞穴主线_1_29` has story_spine
reference 560463 (also 560729 in another scope), original file 558502,
resource `spine_background:850007`. The retained ZIP was downloaded via the
configured storage client and inspected; no production records were modified.

Its manifest contains one skeleton, `shuizhong.skel`, with an authored `idle`
animation. Conversion through the deployed Riglyph CLI succeeded; duration is
approximately 10 seconds. The original prefab canvas is 2560×1440.

The archive has neither `presentation` nor `controls`. Its source prefab is
`/Game/Aki/UI/UIResources/UiCG/Prefabs/CG3_3/UiItem_CG3_3shuizhong`.
`prefab_layout` decodes it, but `visible_layers` rejects its custom material:
`/Game/Aki/Render/Shaders/UI/Spine/LGUI_Standard_SpineMip` and additive variant.
The normal material is unlit/translucent; this observation alone is not shader
parity validation and is insufficient to whitelist it.

Production marks the reference `static_only` with:
- `ui_composition_not_supported`
- `material_semantics_unverified`
- `no_explicit_rig_root`

Across production, 192 story_spine references have static preview status and
one has no preview status. These are references, not 193 unique scenes.
`riglyph_backfill.current` and `candidates` deliberately skip existing static
results unless `--rebuild` is passed. A normal backfill will not upgrade them.
A forced rebuild still uses the single-rig eligibility policy, so it does not
supply the missing prefab/composition support.

## Other confirmed examples

- 2.1 `剧情_2_1_世界_埃弗拉德金库_POI任务_50_1`, resource 700001:
  UI composition and missing explicit rig root.
- Cantarella 2.2 `剧情_2_2_角色_坎特蕾拉线_1_9` and `_8_4`, resource 750001:
  same composition/material/root limitations.
- 2.7 `剧情_2_7_狄斯台地主线_上半_巡游天国_4_1`, resource 800007:
  three skeletons; explicit composition required in addition to materials.

## Music and frontend

The reader only derives background music from a unique published `music` track
in SequenceMedia. The Denia legacy archive does not contain the scene controls
or presentation timeline that would establish that relationship. No song URL
or timing was guessed. The frontend grouping also excludes lines with player
choices, splitting illustrated runs at these lines.

## Validation and remaining work

33 Riglyph backfill tests and 29 LGUI/composition/story-Spine tests passed.
This verifies existing contracts, not successful end-to-end rendering of these
legacy scenes. Full animated prefab composition, music ownership/timing,
publication, and frontend playback must be verified before claiming completion.
Do not blindly enqueue all static references: the current policy will return
static again. Re-extract the authored presentation and verify supported material
semantics, then pilot one exact owner before expanding to other resources.

A separate 10-second native WebM probe was launched from the converted Denia IR
(alpha, 24fps, 1280x720 test output, not a proposed authored canvas). It did not
produce a completed WebM during the bounded check; only a zero-byte temporary
WebM existed. The probe's own render process tree was stopped. Production
workers and queues were not restarted. This does not establish a renderer
crash or numerical/visual compatibility; runtime/performance remains unverified.

## Rollout update (2026-11-08, 01:05 UTC)

The findings above describe the original implementation, before this branch's fixes.

- Native Riglyph 0.16.2 now renders validated legacy single-visible-rig prefabs,
  including Denia's SpineMip materials and authored black background. Original
  skeleton/atlas/textures, presentation, UI sequences and converted IR are retained.
- The reader accepts native-v2 exports, includes choice-bearing illustrated lines,
  and displays animations as looping backgrounds without video controls. Scoped
  flow music can be published independently of 3D engine playback.
- API and web image `0ac32692074d5eb3033674bae6820212cdc9b2f1` deployed successfully
  in workflow 42431991980. Existing media workers were not restarted.
- A separate durable `wuwa.illustrated-backgrounds.v1` consumer runs in
  `solaris-legacy-illustrated-0ac3269` (3 GiB, two CPUs, concurrency one).
- Denia pilot run 32026 found all seven requested resources and is rendering
  its authored 2560×1440 canvas proportionally at 2048×1152, 30 fps. This is
  still an in-progress job, not a successful publication receipt.
- Coordinator dry-run found at least 500 missing/current-recipe jobs. Its
  inventory now includes raw authored backgrounds without any previous media
  reference. Denia, Cantarella and the identified 2.4 image flows have priority.
  Durable receipts live under `/var/lib/wuwa-worker/recovery/illustrated-20261108/`.
- Denia music is a persistent Wwise state consumer, requiring explicit fixed-start
  reconstruction rather than treating its state-setting event as a Play event.
  This remains separate from visual validation and must be verified before
  describing that pilot as having background music.

Publication replaces matching references only after validation, retains sources,
and protects newer recipes against older publishers. No queues were cleared.
