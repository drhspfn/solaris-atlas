# Source-backed media references

The compiler already records game media identities as graph nodes and edges. The quest
transcript API now returns each line's exact `has_voice_reference` and
`posts_audio_event` evidence. `GET /quests/{id}/media?game_version=1.0.0` exposes
quest action `plays_cutscene`, `plays_sequence_asset`, and `posts_audio_event`
references, plus cutscene variants, their `CgFile` assets, audio events, captions,
and quest video package references. Every returned relation is filtered by the
selected release's `graph.edge_evidence` and carries its source file and raw path.

Reference types have different extraction meaning:

| Raw field | Stored reference | What is known |
| --- | --- | --- |
| `PlotAudio.FileName` | `plot_audio_filename` | Exact filename, such as `vo_Character_JiYan_12_1`; no Unreal path is established. |
| `TalkAkEvent.AkEvent`, `PlaySequenceData.Path`, `cgVedio.CgFile`, `videosound.EventPath` | `unreal_asset_path` | Exact `/Game/...` resource path from source data. |
| `QuestRefVideo.PakName + OnlineBranch` | `video_package_identity` | Package and branch identity; no deterministic join to one `CgName`. |

The future FModel worker can request an exact Unreal path directly, while a
PlotAudio filename needs a separate package lookup. A filename must not be
converted to a guessed `/Game/...` path. The current API returns references,
not a playable URL. Once exported media is registered in storage and linked to
its source node, delivery URLs can be exposed separately.
The PlotAudio row also does not establish which localized voice bank contains
the file; the worker must verify that during export.

Flow states in the transcript sidebar are navigable authored groups. They are
not named scenes or a guaranteed playthrough. Likewise, a voice filename and
dialogue line alone do not provide timed subtitle alignment. Timed playback
requires the actual audio file and timing metadata; no durations or timestamps
are inferred here.

## Character dossier media

`GET /characters/{canonical_key}/archive?locale=en&game_version=1.0.0` reads the
selected snapshot's `RoleInfo`, `Skill`, `SkillTree`, `ResonantChain`,
`FavorRoleInfo`, `FavorWord`, and `RoleAudio` records. It returns exact Unreal
paths for portraits, icons, and FavorWord voice events. `RoleAudio` battle cues
are event names only; they are not known file paths. The dossier also returns
localized text, skill tree costs, and source file/row information.

For a browser-ready export, register the file in `storage.file_object` and
`storage.file_location`, then attach a `storage.file_reference` with the
character's `owner_node_id`, the imported `release_id`, and the exact source
`/Game/...` path in `source_path`. For a localized FavorWord recording, set
`source_name` to the locale code (`en`, `ja`, `zh-Hans`, or `ko`). Artwork uses a
null `source_name`. The dossier exposes `/api/media/files/{file_id}` only for
linked, available browser image/audio formats; raw Wwise events and Unreal
texture packages do not become playable merely by registering their paths.
