# Inspecting cooked material shader maps

This is an offline diagnostic, not a material renderer. Preserve the original
client and export inputs in a separate workspace. A material path or `StateId`
is not evidence that a specific shader is used: material instances can have
their own static permutations.

1. Extract the material instance and its `.uexp` with the existing FModel
   workflow. Decode it with CUE4Parse configured for `GAME_WutheringWaves` and
   **`ReadShaderMaps = true` before the package is loaded**. The stock CLI used
   for model extraction does not currently set this flag. The local probe below
   uses the existing worker runtime and .NET 10 SDK; `CUE4ParseDir` points to the
   directory containing the matching worker `CUE4Parse.dll` and its dependencies:

   ```text
   dotnet build scripts/shader_material_probe/ShaderMaterialProbe.csproj -c Release -p:CUE4ParseDir=PATH_TO_RUNTIME
   dotnet scripts/shader_material_probe/bin/Release/net10.0/ShaderMaterialProbe.dll EXTRACTED_ROOT MATERIAL.uasset OUTPUT.json
   ```

   The probe requires exactly one matching filename and does not overwrite an
   existing output. Export the material's
   `LoadedMaterialResources`, including `ShaderMapId`, `ResourceHash` and
   `Content`; do not substitute a parent material's hash for an instance's hash.
2. Extract the relevant `.ushaderbytecode` archive. In the tested 3.7.2 client,
   the main SM5 `Client_Chunk` archive is approximately 1.27 GB; extraction can
   require multiple GiB of memory. Match a shader map against every applicable
   base and patch archive, preserving the source archive and game version.
3. Run `python scripts/inspect_shader_archive.py ARCHIVE --material-json MATERIAL.json
   --feature-level SM5`. Archive version 2 is supported. The tool validates table
   counts and bounds, joins the material's explicit `ResourceHash` with the map
   hash, retains the material quality level, and resolves each material
   `ResourceIndex` to an archive shader index.
   A missing match is not proof the game has no shader: the correct platform,
   patch, or archive may be missing. `FeatureLevel` is not by itself proof of
   the archive's shader platform.
4. Decompress each matched entry using the archive's compression and validate
   the resulting DXBC/DXIL container before disassembly. Select the shader
   role and vertex factory from the material map; do not assume the first pixel
   shader or raw byte offset is the one used by a mesh. Compiled instructions
   are not equivalent to the original editable graph. Texture slots, uniform
   buffers, render pass and runtime state still require interpretation.

## Confirmed Suisui examples

For the small fan's `MI_ESuisuixiaoShuishanMd10011_Shanmian`, the SM5
`ResourceHash` `35FAEF8FBB124DC432224799680FA745CC548992` resolves in the
main Client_Chunk archive to a map with eight entries. For
`TGPUSkinVertexFactoryDefault`, resource index 7 is archive shader index
283116; its DXBC disassembles as `ps_5_0`. For
`MI_ESuisuixiaoShuishanMd10011_Shanmian_Mountains`, hash
`FB0287A008ED64E585102A4D71639C30432A3E8E` resolves to another eight-entry
map, and the same vertex factory's pixel shader is archive index 13852.
These are specific to the inspected client and material permutations; never
hard-code them as identities for another build.

The same procedure also matched body material instances
`MI_R2T1SuisuiMd10011Up01` and `MI_R2T1XinMd10011Up` (Hsin): each has separate
High and Medium SM5 maps, with 78 and 74 referenced shader entries respectively.
This confirms the join works for more than the two fan effect instances, but
does not establish which quality or permutation is active in a particular game
session.

VFX, Niagara, dynamic parameters and a faithful Blender or web shader are not
implemented by this diagnostic. Multiple passes, runtime conditions and
platform variants must be resolved before claiming visual equivalence.
