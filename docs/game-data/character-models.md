# Character-model conversion and queued imports

The first subject is The Shorekeeper (`character:1505`, internal asset name
`Shouanren`). The local experiment below established the conversion recipe;
the queued production pipeline is documented afterward. Keep original packages read-only.

## Verified toolchain

- FModelCLI: `ad969ec899a235b4b5b7706df9244112233dd7de` (raw extraction).
- CUE CLI: `ab447bbcd4e11f426c41c417cacd937fab71c789`, merged with
  CUE4Parse `cdef68791d0830d080e42477c343d873881ad52f`, as in the worker image.
- ACL native source: `414689d5cff4286a7898487a46dc5e48005d38da` and its RTM submodule.
- UEFormat Blender importer: `d858fe38b2b5b8c63ac4b7481b4386ebfabbf935`.
- Portable Blender 4.5.15 LTS. Windows archive SHA256:
  `52dd02fd74877fa08e74b67d7854cf32468a232c02aaa19b6844c91285574f8d`.

The local experiment lives in `E:/Caches/solaris-model-lab/shorekeeper`.
The installed game was read from
`E:/Games/Wuthering Waves/Wuthering Waves Game/Client`, including HD archives
and downloaded hotfix resources. Scanning only `Content/Paks` misses textures.

## Extract a bounded dependency set

Use an existing key file; never put its contents in commands, reports or source
control. Redirect FModel logs to the private experiment directory because error
logs can include key values. Run these commands in PowerShell, with `$FModelCli`,
`$GameClient`, `$KeyFile` and `$Work` pointing at the local tools/game/workspace:

```powershell
& $FModelCli $GameClient "@$KeyFile" "$Work/raw" Shouanren
& $FModelCli $GameClient "@$KeyFile" "$Work/raw" ACLAnim
& $FModelCli $GameClient "@$KeyFile" "$Work/raw" ACLPlugin.uplugin
& $FModelCli $GameClient "@$KeyFile" "$Work/raw" Aki/Character/Role/FemaleM/BaseAnim
```

The last group contains shared moves referenced by Shorekeeper's montages,
including death, movement and second jumps. Nine montages initially failed
without these dependencies. Follow source references when extending the set;
matching a character's name alone does not establish complete coverage.

## Convert to UEFormat and source evidence

Apply `packages/worker/docker/patch_model_tools.py` to the pinned CUE CLI before
building it. The normal worker Dockerfile applies this automatically. It makes
all repeated `-i` roots effective and registers plugin virtual mount paths.

```powershell
& $CueCli -i "$Work/raw/Client" -i "$Work/raw/Engine" `
  -g GAME_WutheringWaves -p '*.uasset' --mesh-format UEFormat `
  --compression None --mesh-quality Highest -o "$Work/converted"
& $CueCli -i "$Work/raw/Client" -i "$Work/raw/Engine" `
  -g GAME_WutheringWaves -p '*.uasset' -f json -o "$Work/source-json"
```

For the actual experiment, the large conversion preceded the shared dependency
repair; only the nine missing montages were then re-exported using `-c` and a
package list. Repeating the whole patch extraction is unnecessary.

Mount **Client and Engine separately**. Their directory names define Unreal
mount points. A parent directory named `raw` breaks `/Game` dependency lookup.
`Engine/Plugins/Animation/ACLPlugin` must include its real `.uplugin` descriptor
and compression settings. Otherwise the CLI can write a tiny `.ueanim` with no
tracks while returning exit code zero. The worker image now checks published
ACL native symbols and loads the library to verify ACL support.

On Windows, build the CLI with .NET 10 and `PublishTrimmed=false`. Build the
pinned `CUE4Parse-Natives` with its ACL/RTM submodules using CMake and Visual
Studio C++ tools, then place `CUE4Parse-Natives.dll` beside the CLI. Disabling
native compilation is only valid after supplying this verified library.

## Assemble a browser bundle

`model_bundle.py` runs inside Blender and imports the pinned UEFormat plugin.
Use `--python-exit-code 1`; Blender otherwise can exit successfully after a
Python exception.

```powershell
$Role = 'Client/Content/Aki/Character/Role/FemaleM/Shouanren/R2T1ShouanrenMd10011/Model'
& $Blender -b --factory-startup --python-exit-code 1 `
  --python packages/worker/src/wuwa_story_worker/model_bundle.py -- `
  --model "$Work/converted/$Role/R2T1ShouanrenMd10011.uemodel" `
  --converted "$Work/converted" --source-json "$Work/source-json" `
  --skeleton '/Game/Aki/Character/Role/FemaleM/Shouanren/R2T1ShouanrenMd10011/Model/R2T1ShouanrenMd10011_Skeleton' `
  --ueformat-plugin "$UEFormat/plugins/blender" --output "$Work/bundle"
```

Add `--animation Stand1` for a smaller first verification. Output:

- `character.glb`: mesh, skin, textures and compatible non-additive animations.
- `character.blend`: editable model, packed textures and imported actions.
- `report.json`: source model hash, skeleton identity, per-clip source paths,
  frame/track counts, additive flags, GLB inclusion and missing source exports.

The source skeleton reference determines ownership. Files inside a Shorekeeper
sequence folder can belong to another character, a butterfly or a prop. Additive
clips require a base pose and are retained in UEFormat rather than incorrectly
played as independent body animations. Morph targets remain on the model, but
facial curve animation, montage events, cloth physics and game shaders are not
reconstructed by this preview. Materials use the exact exported `MainTex` path;
packed shader masks are not guessed to be standard PBR maps.

Validate the GLB with Khronos glTF Validator and inspect browser playback.
The first textured idle bundle had zero validator errors; its one warning was
`NODE_SKINNED_MESH_NON_ROOT`. Extra UV sets were reported as unused information.
The initial base-only client export had 243 bones; including local hotfixes
produced the current model with 250 bones, 59,675 vertices and eight materials.
Do not combine files from these two resource sets.

The complete local skeleton-linked inventory contains 504 source animation
assets and 542 exported clips (montages can produce multiple clips). All 504
sources have an export after dependency repair. There are 250 non-additive body
clips, two curve-only facial clips, 281 local-space additive clips and nine
mesh-space additive clips. Curve-only facial files are not empty animations.
`animation-inventory.json` in the experiment directory records their source
paths, frame rates, frame counts, bone tracks and facial-curve counts.

The verified full browser GLB is 146,652,728 bytes and includes all 250
non-additive clips: 249 moving animations and the static `Tanqin01` pose.
It retains 250 skin joints, 86 morph targets and seven embedded textures.
Khronos validation reports zero errors and one `NODE_SKINNED_MESH_NON_ROOT`
warning. The optimized full export took 209.8 seconds on the local Windows
machine. The separate 159,207,263-byte source archive retains all 542 clips,
source metadata and file hashes; additive and facial layers are preserved
there without claiming their in-game composition is reconstructed in GLB.

For large collections, constant one-key channels trigger Blender's fallback
sampling even when `export_force_sampling=False`. The assembler extends these
constant channels with an equal end key, preserving their value and duration.
It also uses Blender's viewport optimization during armature export, retains
bone resets, and disables morph animation because facial curves are not imported
by this preview. Static morph targets remain on the exported model.

Profiling the large export also identified quadratic root-reference interning
in Blender's `GlTF2Exporter.__append_unique_and_get_index`. The assembler uses
a scoped identity index for its append-only accessor and buffer-view arrays.
Those pinned generated classes use identity equality; other types keep the
original equality behavior. A guard checks the pinned implementation, and the
original static method is restored after export, including on failure. Blender's
installed source files and the interactive user's settings are not modified.

Serve only a separate preview directory containing GLB and viewer assets on
`127.0.0.1`. Keep keys, original packages and private logs outside that directory.

The reproducible [local mini viewer](../../tools/model-viewer/README.md) adds
orbit/zoom, an optional ground grid, playback controls and switchable secondary
bone springs. Its profile is extracted from the model's KawaiiPhysics blueprint;
it preserves source groups/limits but approximates runtime physics rather than
reproducing Kuro's cloth/collision solver. It does not modify or enlarge the GLB.

## Queued model imports

The admin data page and admin actions on character profiles enqueue
`POST /admin/character-models` with `character_key`, optional `game_version` and
optional `force` and `include_animations` (default true). Admin authorization and CSRF are required. The response is an
inspectable processing run; its status is available from
`GET /admin/character-models/{run_id}`. Repeated active requests reuse a run.
Completed requests require `force` to rebuild. There is no automatic 61-character
bulk export. Legacy queued jobs without `include_animations` remain rig-only.

The dedicated `character_model` consumer reads the selected release's
`BinData/role/roleinfo.json` MeshId and its exact
`BinData/model_config_preload/modelconfigpreload.json` mesh/blueprint references.
It does not infer models from portraits or character names. Composite rigs fail
with an inspectable error rather than silently selecting an unrelated mesh.
Physics blueprints must target the mesh's exact exported skeleton.

Downloaded archives are shared read-only. Rig-only imports export the model
directory and authored blueprints. Animation imports additionally export the authored
character directory, shared gender base moves and ACL conversion dependencies;
only clips matching the model's skeleton are admitted. All exports use an owned
transient workspace. Conversion, R2 writes
and atomic FileReference publication complete before scratch is removed; crash
recovery collects orphaned transient directories. Source package paths/hashes,
client identity and tool/recipe hashes remain in reference metadata. No raw
texture tree or Blender working file is uploaded.

The default bundle contains:

- `preview.glb`: rig, base-color textures limited to 512px, no animations,
  facial morph targets, unused UV layers or vertex colors.
- `character.glb`: full-resolution textured rig retaining morph targets and
  original UV/color attributes, with no animations. Blender can import/edit
  this GLB; KawaiiPhysics/MMD physics do not become Blender constraints automatically.
- Optional spring profile JSON, with source group/limit evidence and explicit
  approximation limitations.
- `animations.glb`, when requested: compatible non-additive skeletal clips and
  skeleton nodes, without duplicated geometry or textures. Additive layers,
  facial curves, montage events and game cloth simulation are not reconstructed
  as standalone body clips. A failed requested animation export fails the run;
  it does not replace a published bundle with a silently incomplete result.

The verified Shorekeeper outputs are 5,974,204 bytes for the preview and
20,107,368 bytes for the editable model. Both have 250 joints and seven embedded
textures. The full model retains 86 morph targets. Sizes vary by character;
these measurements are not a universal storage budget. Identical object hashes
are reused across repeated imports and releases.

`GET /characters/{key}/model?game_version=...` returns only bundle metadata.
The character page mounts the viewer and downloads the preview only after
**View 3D**. Closing it unloads the iframe. The full GLB is a separate explicit
download, with an attachment filename ending in `.glb`. Animation metadata belongs
to the same publication as the rig. **Load animations** explicitly fetches the
separate pack; opening a model never downloads it automatically. The pack can
also be downloaded separately and imported alongside the rig in Blender.

The worker publishes a hash-versioned viewer beside the models on the same CDN
origin, avoiding cross-origin GLB fetches and API byte proxying. The viewer index
is uploaded last; model references share one publication identity. Local
viewer assets remain available for development. Static rigs hide animation
controls; optional turntable rotation can exercise springs without animation clips.

## Dedicated Linux model-tools image

Blender is deliberately separate from the ordinary production worker image.
Build the worker from this revision first, then extend that immutable image:

```sh
docker build -f packages/worker/Dockerfile -t solaris-worker:model-base .
docker build -f packages/worker/Dockerfile.models \
  --build-arg WORKER_IMAGE=solaris-worker:model-base -t solaris-worker:model-tools .
```

The optional image pins the Linux Blender archive SHA256, pins the UEFormat
importer and runs its import smoke check offline as the runtime user. It exposes
`WUWA_BLENDER_PATH` and `WUWA_UEFORMAT_PLUGIN`. The normal image build remains
unchanged in size. Run the images workflow manually with `component=models` to
build a pinned worker base and validate the optional model-tools image. This
manual workflow does not deploy services. Main image builds now also publish the
separate models image before deployment. Only `model-worker` uses that image;
ordinary image/audio workers retain their existing runtime. Model concurrency is
one per replica, controlled by `WUWA_MODEL_REPLICAS` (default one).
Both Linux builds and the offline plugin smoke check passed in run
[`40574532763`](https://github.com/drhspfn/solaris-atlas/actions/runs/40574532763).
Actual game-model conversion and browser playback were exercised on Windows;
the CI smoke check does not mount or publish the user's game assets.
