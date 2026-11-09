# Character model skins and Rover appearances

Model recipes now include `BinData/role/roleskin.json` records matched by
`RoleId`, alongside the role's `SkinId`, gameplay `MeshId`, display `UiMeshId`
and `rolemorph` records. Every preload and translated skin title is read from
the requested release. English `TitleName` values label the existing form
selector; if localization is absent, the stable skin ID is displayed.

The queued worker, recipe download and local `wuwa-model` build share
`model_recipe` and `resolve_recipe_forms`. Source role nodes are not merged or
deleted. New skins are discovered from table rows, not guessed asset paths.
Morphs with an explicit `SkinId` only apply to that skin. Distinct morph UI
meshes are resolved too, subject to the same authored character-root check.

Rover membership and gender come from `main_role_change/mainroleconfig.json`,
using the existing public-identity rules. One requested Rover recipe includes
male and female variants across all declared elements. Models with identical
mesh and component paths are exported once; distinct element meshes remain.
Labels identify gender and skin. New element IDs require no ID-range update.

## Verified source inventory

Dry resolution against the extracted 3.7 tables found:

| Character | Resolved appearances |
| --- | --- |
| Jinhsi, Changli, Mornye, Lynae, Chisa, Sanhua | Base and alternate skin each |
| Zani | Base normal/battle, skin normal/battle |
| Carlotta | Base and skin; each includes separate gameplay and display composition |
| Hiyuki | One preload declared in these role tables; no alternate skin or morph row |

Carlotta's display preloads explicitly list `Body` plus sibling `Hair1` meshes
for both costumes. The resolver admits this body/hair layout and retains all
parts. Export must still validate the component skeleton, hierarchy and rest
pose; unrelated composite rigs remain unsupported. This source check does not
claim a newly rendered GLB was visually verified.

Hiyuki may have additional Blueprint-level appearances; a future 3.8 skin is
not inferred from these 3.7 records. Authored skin and morph records will be
discovered when that release is imported.

## Rebuilding

No schema migration or automatic mass reimport is introduced. Existing model
publications stay available. Rebuild a character with `force=true` to publish
its expanded appearance bundle. Download a fresh recipe before a local build:
old archives that omit newly required appearances fail source verification
rather than replacing a complete bundle with a partial one. All forms must
validate before atomic publication; the previous publication remains intact
on failure.

Regression coverage includes Zani's four appearances, Rover gender/element
deduplication, skin-specific morphs, future synthetic skin morph records,
missing or invalid mesh references, release-scoped recipe queries and legacy
recipes without a skin table. Four existing model-animation test failures were
reproduced against unmodified `main`: their mock workspaces lacked the pinned
client receipts now required by extraction. The fixtures now provide those
receipts and exercise child-workspace inheritance.
