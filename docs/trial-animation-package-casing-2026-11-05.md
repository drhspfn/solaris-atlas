# Trial portrait animation: package casing mismatch

Verified on 2026-11-05 against retained production source archives from client 3.7.1.

The stored `decode_failed` / `no_complete_spine` decisions were misleading: decoded
JSON, skeletons, atlases and textures are present. `archive_policy()` reconstructed
the decoded JSON's full Unreal path, then compared it case-sensitively to the
manifest's authored path. The exporter preserves cooked spelling, which differs
from the authored reference. This is an Atlas validation bug before Riglyph runs,
not evidence of a missing Spine feature or renderer error.

| Character | Node | Canonical key | Source file ID | Authored portrait spelling | Decoded spelling |
| --- | ---: | --- | ---: | --- | --- |
| Denia | 5322879 | character:1211 | 571455 | DaNiYa | Daniya |
| Hiyuki | 5322888 | character:1108 | 571499 | FeiXue | Feixue |
| Yangyang-Xuanling | 6094623 | character:1610 | 571555 | XuanLing | Xuanling |
| Suisui | 6094624 | character:1110 | 571590 | SuiSui | Suisui |
| Qingxiao | 6094630 | character:1413 | 571605 | QingXiao | Qingxiao |
| Jingran | 6094632 | character:1212 | 571625 | JingRan | Jingran |

The sixth entry arrived while imports continued after the overnight check.
The original decoded files determine spelling; use the manifest inventory for
exact file paths rather than constructing replacements from this table.

Denia example:

- Authored package: `/Game/Aki/UI/UIResources/Common/Spine/Portraits/Portraits_DaNiYa/Portraits_DaNiYa`
- Existing JSON: `decoded/files/Client/Content/Aki/UI/UIResources/Common/Spine/Portraits/Portraits_Daniya/Portraits_Daniya.json`
- Retained archive: `objects/df/7e/df7e7406531c8d062ecb5392fd92ea8ba581d193768361c0463d3d333d593d8e`

## Fix and verification

Match complete package paths case-insensitively. Reject ambiguous case-colliding
manifest packages; retain checksums, path validation and composition checks. A
similarly named file in a different directory must not satisfy the policy.

All six actual ZIPs reproduce the rejected decision with the old policy and pass
source eligibility with the corrected policy. Eligibility alone is not successful
animation rendering. Denia was additionally converted and rendered through the
existing worker pipeline using Riglyph 0.16.1 native CPU: an alpha VP9 WebM, idle,
4 seconds, 1228 by 2048, with complete renderer validation. The other five have
source-policy verification, not a verified new native render.

The character profile now hides the entire trial section, including its download
link, unless a validated playable idle WebM exists. Existing artwork is retained.
Playable previews are bounded to 480px wide and 220-320px high, maintaining video
proportions with contain fitting. Network playback errors still retain the shared
player's retry action.

## Recovery

After deploying the worker fix, use the existing R2 animation backfill for these
source references only. Keep the original archives and file IDs; no game download
or image export is needed. Rebuild affected character read projections only when
new preview references are published. No production re-render or replay was run
as part of this investigation. Unrelated unsupported story/event compositions
must continue to use static fallback.

## Linux follow-up during deployment

The first production backfill exposed a second casing mismatch that Windows does
not catch: the atlas requests `Portraits_Daniya_2.png`, while the retained archive
contains `Portraits_DaNiYa_2.png`. Linux conversion failed with `ENOENT` before
rendering. The converter now creates an exact-name temporary alias only for a
checksum-verified texture at the same full atlas-relative path. Original bundles
and atlas contents remain untouched; ambiguous case collisions and unsafe page
paths are rejected. This belongs to the Atlas import adapter, not Riglyph's Spine
feature support.

A separate, genuine renderer failure was observed for **Hiyuki**, source file
571499, trial reference 721350, after source conversion succeeded:

- Riglyph native CPU 0.16.1, animation `idle`, 120 frames planned at 30 fps.
- Frames 1-49 rendered; failure near animation time 1.6 seconds.
- Code `INVALID_TOPOLOGY`.
- Message `Clipping polygon self-intersection at edges 4/6`.
- Durable worker log: `client-assets/render-logs/50c5c264859468bd1ad42ea2a719ab40cb7cf7fdeaae320581bfc16c6b1cede7/render-80f78c9053e444519529cffcdeeecf48/000.log`.

This is a useful reproduction for the next Riglyph version. No partial video was
published; the converted fallback and original source archive are retained. Do
not treat the other five characters as having this geometry failure without
running their own renders.
