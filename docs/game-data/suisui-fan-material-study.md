# Suisui fan material study

The cooked character blueprint `BP_R2T1SuisuiMd10011` contains two instances of
`ESuisuixiaoShuishanMd10011`: `OtherCase1` attaches to `WeaponProp01` and
`OtherCase6` to `WeaponProp02`. Both components default to hidden. In the
`AM_Attack01_1` montage, `OtherCase6` is switched to `WeaponProp02`; this is
animation-specific evidence, not proof of a fixed visible state for every pose.
The fan has an independent skeleton and separate left and right animation files.

`scripts/suisui_fan_material_study.py` creates a local **experimental** Blender
scene with the left fan rig attached to the first blueprint socket. It imports
the fan's left `Stand2` animation and maps the `Base` texture from each fan
material instance to a Principled shader. The water transparency multiplier is
an artistic preview value, not a recovered game parameter. The fan is shown in
the preview even though the original component defaults to hidden. Body/fan
animation synchronization, animated visibility, slot switching, parallax,
refraction, matcap, flipbook normals and screen-space hair effects remain
unimplemented. The preview is not an Unreal-to-Blender shader translator.

The extraction used the explicit `Base` references from the cooked material
instances: `T_Tile_30001` for water and
`T_Wenli_Suisui_FolderFan_15002` for the mountains. Other game texture and
parameter names exist, but naming alone does not establish the graph operations
or bindings in compiled code. See `shader-map-inspection.md` for locating the
compiled permutations. Validate a reconstruction with matching game poses,
lighting and effects before promoting it to the importer.

## Inspectable material inputs

`scripts/blender_material_inputs.py` can create a separate Blender project
from existing converted material-instance JSON and extracted PNGs. Pass the
original `.blend` with `--scene`, each JSON with `--metadata`, one or more
converted roots with `--textures`, and new paths for `--output` and `--report`.
The JSON filename must match an existing material in the scene. It uses exact
`/Game/` or `Client/Content/` paths and refuses to overwrite outputs.

For each material, the builder connects only `MainTex`, `BaseColor` or `Base`
to a standard Principled surface; it also creates labelled, unconnected nodes
for other available texture parameters and preserves scalar, vector and switch
values as material properties. The report lists missing parameters. This is an
input inventory with a diffuse fallback: it does not translate bytecode,
interpret alpha channels or recover the original render graph. Do not use its
output as a replacement for a verified game material.

With `--approximate`, the builder adds explicitly labelled, adjustable preview
nodes: positive-Z reconstruction from the R/G normal channels when the instance
enables normal mapping, a roughness and unlit fill for hair, and a simple
translucent effect preview with a second layer when the instance enables it.
These formulas are hypotheses, not recovered game code. The report records
which approximations were applied. Backface hair fill was tested but did not
resolve the dark ponytail in the controlled rear render and was discarded.

The ponytail's opaque hair diffuse texture has alpha values below 1. Blender's
default straight-alpha interpretation darkened its RGB even in an emission-only
test. For opaque or masked character instances with `UseHairVCGradient` or
`UsePBR`, the builder makes an independent image copy using `CHANNEL_PACKED`;
an identical rear render then retained the light hair colour. The original
image and translucent effect materials are unchanged. The masked material's
alpha still needs an independently verified use in the shader. This
does not establish the exact purpose of alpha or implement transparency over the
eyes. Blender describes channel-packed RGB and alpha as independent data in its
[Image API](https://docs.blender.org/api/current/bpy.types.Image.html).
