# Voice import sample

The worker can pin and download all four Windows voice languages (en, ja, ko, zh), including character packages. `plan-voices` reads the exported Kuro public config and a local configuration crypto file containing base64 `key` and `iv` values. Keep this file outside version control. The resulting plan contains public archive paths, sizes and hashes only.

`download-voices PLAN --workspace DIRECTORY` downloads from official game CDNs. Optional `--installed-game DIRECTORY` reuses installed files only after size and SHA-1 validation; production does not require an installed game. Completed files are reused when restarting the same plan.

Base archives are downloaded from the package version route (for example 3.7.0), whereas patches use their resource version. Locally, base archives remain in the `Base` directory used by the game.

For a bounded sample, extract an exact PlotAudio filename prefix with FModelCLI from the completed voice mirror, using the existing client key file. Then publish extracted files:

```powershell
uv run --project packages/worker --env-file packages/worker/.env python -m wuwa_story_worker.voice_import EXTRACTED_DIRECTORY --decoder VGMSTREAM_EXECUTABLE --asset-version 3.7.0
```

The importer requires all four languages for each extracted filename, matches existing VoiceReference records exactly, decodes WAV, and registers original WEM and playable WAV files through the existing content-addressed storage service. FileReference metadata stores language, duration and asset version. Repeated publication updates the same voice/language/build reference under a database advisory lock.

The initial local sample contains ten voice references (40 tracks); eight of these appear at the beginning of quest 139000025. Audio comes from the 3.7.0 client, while the selected text snapshot can be 1.0.0. Historical recording identity is not verified. Unmatched extracted filenames are not attached to dialogue.

This is a CLI sample flow. Automatic extraction, queue orchestration, full-catalog coverage, compressed browser variants and partial-download resume still need production work. Do not treat the sample as a finished automated production pipeline.

## Caption-triggered cutscene voices

Cutscene caption speech is a separate source path from `VideoSound`. The inspected
client `Game/Module/Video/VideoView.js` calls `_iy` when `JNo` displays a caption.
Only `IsPlaySubtitleVoice=true` authorizes `PlotAudio.Id = CaptionText`; `_iy` posts
`play_external_vo_video_subtitle_level_b` with the filename returned by
`Game/Module/Plot/PlotAudioModel.GetExternalSourcesMediaName`.

The worker follows the exact PlotAudio filename, language-specific `CheckGender*`
flags and `GlobalLanguage`, using the declared `VideoData.GirlOrBoy` for distinct
video variants. `VideoConfig.GetVideoCaptions` selects localized timing as a whole
list, falling back to base timing only when that localized list is empty. Starts
use `VideoUtils.MillisecondPerFrame=33.33`; `Caption.Duration` hides text and does
not stop speech. Caption voices are separate external-WEM inputs to the existing
video-owned audio bundle; original WEMs enter its durable publication closure.
Legacy bank-only recipe serialization and hashes are unchanged.
Direct caption WEM decoding does not establish bit-identical Wwise external-event
DSP or Init-bus gains; those have not been verified by this source-path fix.

Evidence in `tests/fixtures/caption_voice_local_3_7_0.json` is from a **local
installed-client export**, whose Resources directory was named `3.7.0`. It records
source DB SHA-256 values. Production build identity and byte equality with
production `3.7.1` JS, configuration, or banks have **not** been established by
this fixture. Runtime imports use their own pinned client and package identities.

In that local fixture, all 21 M0366 captions have a false voice flag: a matching
PlotAudio name must never inject caption speech into that movie. `M3_10_04` has
23 enabled captions, producing 92 localized inputs per distinct video variant.
The two enabled `M3_10_08B` captions have no exact PlotAudio rows and remain explicit
missing-source diagnostics. Templated filenames needing runtime world state,
missing/ambiguous external files, and gender-dependent speech on a shared movie
without an explicit gender remain partial rather than selecting a substitute.

Fresh, scoped cutscene preparation records which CGs actually enable caption
speech. Their leaves require `caption_audio_schema=1` publication coverage, so a
legacy complete video cannot suppress the newly required caption audio. Existing
unrelated completed publications remain reusable; no blanket regeneration or
automatic production backfill is performed. Recovery of an old caption-enabled
publication requires this fresh targeted preparation, not replay of an old saved
payload that lacks the caption coverage requirement.

## Authored character voice alternatives

Simple v172 State/Random voice events can retain all independent alternatives.
The worker exports exact event BNKs and Init from its pinned main-client plan,
verifies receipt hashes, matches the complete cooked media inventory, then runs
WWiser with Init. Only single-source renditions with no additional DSP/layering
or sequencing are accepted. Unknown selectors and unsupported recipes remain
explicitly unresolved. No gender, random child, or default state is selected.

Each alternative has its own `character_voice` reference named
`<language>:hirc1:<stable digest>`. Identity uses event/language/sound/media and
state/random container-child IDs, never debug filenames or playlist positions.
Selector branches (including aliases), random weights, bank/Init/tool hashes and
the verified rendition bodies remain in `game_source.voice_variant`; original
BNK, Init and WEM bytes are linked through FileVariant provenance. A retry counts
the language complete only when every member of the same proven set is present.

Character archive voice lines add `audio_variants`: independent entries with
`id`, `source_name`, `language`, `selector_branches`, `url` and `status`.
Missing siblings remain visible with a null URL. Languages with authored
alternatives are excluded from the single-choice `audio_url`/`audio_tracks`;
other languages retain their existing behavior. There is no SQL migration.
Each published alternative queues only its owning character's current and exact
historical read projections in the same transaction as the file reference.
Already-published skips do not enqueue a refresh; no full rebuild is required.

`character-voice-hirc-local.zip` contains seven local-client banks, Init, actual
WWiser XML/renditions and source hashes: five gender-dependent language pairs
and twelve random pairs, 34 alternatives. It does **not** establish production
bank equality. Runtime eligibility is recalculated from the actual pinned
production inputs before any variant publication; this change does not enqueue
a global backfill or publish the local fixture.
