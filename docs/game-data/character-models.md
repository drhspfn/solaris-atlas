# Local character-model experiment

The first subject is The Shorekeeper (`character:1505`, internal asset name
`Shouanren`). This is an offline conversion experiment, not a new production
import queue or a published character viewer. Keep original packages read-only.

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

## Optional Linux model-tools image

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
manual workflow does not deploy services. Both Linux builds and the offline
plugin smoke check passed in run
[`40560320871`](https://github.com/drhspfn/solaris-atlas/actions/runs/40560320871).
Actual game-model conversion and browser playback were exercised on Windows;
the CI smoke check does not mount or publish the user's game assets.
