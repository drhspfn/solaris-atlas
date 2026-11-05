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
