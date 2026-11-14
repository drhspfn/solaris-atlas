# Denia: additive animation investigation

Evidence: `E:/Caches/solaris-model-lab/eye-validation-20261111/denia-full/plan/animation-plan.json` and the corresponding `source-json` and `converted` directories. This is one local export, not a claim about all characters. The animation plan has 1,065 matching decoded sequences: 746 accepted body clips and 319 marked additive. All 319 have 354 decoded bone tracks; 261 also report one or more float curves. Track count alone does not establish that a track changes or is facial.

## Authored additive reference

| Additive type | Reference pose type | Count | Evidence |
| --- | --- | ---: | --- |
| Local space | Skeleton reference pose | 293 | Includes `Expression_Daniya_Accidie` |
| Local space | Frame of another sequence | 13 | `RefPoseSeq`, e.g. `Rib_Sit_Stand` |
| Local space | Scaled reference sequence | 4 | `RefPoseSeq` = `Rib_XA_Loop_Stand` |
| Mesh-space rotation | Skeleton reference pose | 9 | `AimOffset/AO_LookAt_*` |

All 17 reference-sequence paths resolve to source JSON files in this export. For the 13 frame references, `RefFrameIndex` is omitted from the serialized properties; an omitted value is not by itself proof of the runtime default or that the referenced frame can be recovered. Some clips reference a sequence whose path is their own: resolving paths must detect cycles. The UE documentation defines these reference modes and distinguishes local-space from mesh-space composition: [EAdditiveBasePoseType](https://dev.epicgames.com/documentation/unreal-engine/API/Runtime/Engine/EAdditiveBasePoseType), [Animation Sequence Editor](https://dev.epicgames.com/documentation/unreal-engine/animation-sequence-editor-in-unreal-engine), and [Apply Additive](https://dev.epicgames.com/documentation/unreal-engine/animation-blueprint-blend-nodes-in-unreal-engine).

The reference used to calculate an additive delta is not necessarily the runtime base pose. Runtime combination, weight, timing, bone masks, and aim parameters come from the animation graph and need separate evidence. A rest-pose preview could be provided as such, but cannot be called the authored gameplay result. Mesh-space rotation requires hierarchy-aware conversion; applying its quaternions as ordinary local animation is incorrect.

## Curves and existing export

`model_bundle.py` excludes additive skeletal animation but passes its curves to `model_facial.facial_clip`, which matches curve names to exported morph names. In this dataset, 39 additive sequence names have standalone facial clips in the plan, including `Expression_Daniya_Accidie` with `E_Insipid`, `Pupil_Scale`, `B_Flat`, and other exact morph targets. The browser has `engine/facial.js` for the exported facial format. This establishes data presence and a playback implementation, not that the running site has published or correctly displays all 39. Blender packaging and runtime playback for these exact clips were not verified here.

Non-facial float curves must not automatically be treated as morphs. For example, `Kawaii_alpha_S08` occurs in 215 additive sequences, while the verified face extraction requires an exact target name. The present count of 39 represents clips with at least one matching target, not a count of exclusively facial sequences: these clips also have decoded bone tracks.

## Remaining evidence needed before implementation

1. Verify whether `.ueanim` bone values are already additive deltas or converted absolute poses, against an independently decoded UE sequence and its reference pose. Establish translation, quaternion multiplication order, scale convention, coordinate conversion, and sampling on concrete bones and frames.
2. Resolve omitted/default `RefFrameIndex` using the matching game engine version and ensure referenced sequences are decoded with matching skeleton and valid frame bounds; detect cycles.
3. Recover the actual animation graph's input pose, blend weights, bone filters, timing and Aim Offset coordinates. Without them, preserve metadata and expose additive sequences as explicitly labeled layers or rest-pose previews only, not as final gameplay animations.
4. Validate Blender action layering and viewer evaluation against the same authored examples, including facial curves, bone movement, and mesh-space rotation. Preserve original source and reference metadata in the package; do not rebuild existing animation packs merely to run this investigation.

No importer, viewer, production queue, or running task was changed by this investigation.
