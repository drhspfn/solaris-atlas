# Cutscene runtime evidence

## Scope of evidence

The inspected JavaScript came from the locally installed client; its byte
identity with production has not been established. The seven Wwise banks and
Init bank were subsequently verified against the pinned production 3.7.1
archives: all 210 local archives matched the download plan's MD5 values, and
the two actual source archives matched production SHA256 values. The runtime
validator verified 17 event/language pairs, 34 independent alternatives and
45 WWiser renditions against the retained production cooked inventories.
Receipts are in `scratch/ambiguous-voice-hirc/production`. This source proof is
separate from successful publication; it is not permission to hard-code
selector overrides or claim that every variant is already playable.

## Video and voice are separate timelines

In `Game/Module/Video/VideoView.js`, the video-open callback schedules the
`VideoConfig.GetVideoSounds(CgName)` records. `kdp` posts each audio event;
`OnTick` starts and stops events against the video clock. `StartMoment` and
`EndMoment` are converted from seconds to milliseconds. `dmS` seeks a delayed
audio start to the video clock.

There is also a separate subtitle-triggered voice path. `_iy` checks
`IsPlaySubtitleVoice`, resolves `CaptionText` through `PlotAudioById`, obtains
the external media name through `PlotAudioModel.GetExternalSourcesMediaName`,
and posts `play_external_vo_video_subtitle_level_b`. Therefore:

- A caption does not necessarily have its own voice playback.
- A video without a `VideoSound` mix is not necessarily silent in the game.
- Exporting captions and PlotAudio records separately does not prove their
  playback relationship has been published.
- Subtitle-triggered voice must retain its own timing and selection evidence;
  it must not be added indiscriminately to a mix that already contains speech.

The importer review must verify both paths through preparation, publication,
stored references and the public response.

## Character voice variants

The inspected banks explain 17 previously ambiguous event/language pairs:
five use a Rover gender state and twelve use random containers with two possible
outcomes. These are alternatives, not consecutive clips. Equal numeric MediaIds
between local bank sources and production cooked events do not establish equal
control graphs or equal bus processing.

The current singleton character-voice reference uses event path and language as
its identity. Publishing several alternatives into that same reference would
overwrite previous alternatives. Support must retain a stable selector identity,
its source evidence, and each alternative separately before exposing a choice.
Do not use WWiser output-list position as a durable selector identity. Missing
Init-bank dependencies also prevent claiming faithful rendered bus gains.

## Quest transitions

`ChildQuestNodeBase` binds `TrackLevelPlay`, handles activation/end lifecycle and
submits node completion through `GeneralLogicTreeController`. The retained
`ParallelSelectNode` wrapper identifies a node type but does not establish a
single sequential traversal. Authored child paths and asynchronous flow calls
must remain distinct from a global chronological order.

M0334 recovery verifies the particular quest-node/LevelPlay relationship and
individual MP4 fragments. A related encounter can be valid even when its exact
position in an older complete quest tree is unknown. Publishing the fragments
does not turn the entire engine sequence into a complete video.

## Presentation follow-up

After runtime publication is verified, keep the evidence accessible in details
without repeating implementation terminology throughout the quest. Group genuine
alternatives, name navigation entries by meaningful objectives, preserve deep
links, and avoid rendering empty control nodes as separate story activities.
Unknown order must remain honest, but can be explained once per group rather
than after every action. Repeated titles alone are insufficient evidence to
merge different occurrences.

## Historical current-release runtime check, 2026-11-05 21:12 UTC

A bounded read-only check on API commit `15c231f` compared the complete stored
English transcript and story blocks against freshly derived source records for
release 21 (game snapshot 3.7.0, client assets 3.7.1):

| Quest | Source dialogue / stored dialogue | Encounter node occurrences | Duplicate occurrence identities |
| --- | ---: | ---: | ---: |
| 4688653 / 114000031 | 517 / 517 | 186 | 0 |
| 5389571 / 119000000 (Denia) | 654 / 654 | 0 | 0 |

All 37 nested encounter flow occurrences in the first quest matched their exact
source pointer, state and presentation hash. All 46 and 76 block-level flow
references respectively resolved in the selected release. The repeated flow
3_29_1 originates in different LevelPlay nodes (56 and 58); title or state equality
is not sufficient reason to delete an occurrence.

This verifies stored identities, not complete media playback. Fourteen cinematic
flow occurrences in quest 4688653 still had no release-21 presentation publication.
At that check, the quest condition for dungeon 1557 had no matching LevelPlayData
membership and remained unresolved; the later verified exit resolves that finding.
Neither case should be hidden by assigning
an invented order or borrowing a different release without matching provenance.
Null choice destinations require separate interpretation; all non-null destinations
in the checked scope existed. No production replay was performed by this audit.

## Follow-up implementation and bounded repairs

The exact current-release source repair for thirteen states inserted fifteen
missing sequence links and fifteen revisions after checking all thirteen source
hashes. M0334 owner 4648369 was excluded; its queued run 26922 must not be replayed
or duplicated. These links do not establish that every referenced media file has
been published.

Game `VideoConfig.GetVideoSounds` selects the complete shared soundtrack list
when present, otherwise the gender-specific list. The importer now follows that
precedence. C0015 uses the same movie for both Rover variants; recipes retain
both selectors and publish independent audio stems. Completion checks require
every expected role/language/Rover combination and all silent video references.
Legacy playback flows can recover their selector from the authored incoming
choice, without choosing a default while the user preference is Ask.

Segment comparison checks the fallback mix, not all localized stems. Therefore
localized or gender-dependent variants retain their complete authored movies;
sharing a visually identical outro must not substitute another variant's audio.

R0012 has an authored direct MP4 reference. Preparation and publication now accept
that exact path without manufacturing an Unreal wrapper. Missing bytes still
produce a partial result; another movie is never substituted.

The character voice importer retains verified state/random alternatives under
stable selector identities and invalidates the affected character projection in
the publication transaction. Older story writers cannot downgrade a newer proof
for the same pinned source identity. This guard requires updated writers; an old
running container does not acquire the protection automatically.

Validation for backend commit b024a82: 290 worker tests passed, one integration
test skipped; 65 targeted server tests and four audio-selection tests passed;
TypeScript and targeted Ruff checks passed. Manual image build 42118221095 was
started from that branch without automatic production deployment. Production
gender-stem publication and new-image playback remain separate verification gates.

## Selective deployment and persisted graph verification, 22:17 UTC

API and one previously exited image-worker slot now run the verified b024a82
artifacts. Fourteen other active workers were left untouched. This is a selective
backend rollout, not a complete PR deployment or an upgrade of the projection
consumer.

The new API verified all 517 and 654 canonical dialogue identities for the two
quests above, with no missing, extra or duplicate IDs. All thirteen repaired
states retained their exact presentation hashes, fifteen source edges and
fifteen revisions. Dungeon 1557 is now a verified exit to map 1555, while the
original boss encounter retains its two LevelPlay trees.

Only quest 4688653 had a stale current projection. The normal filtered backfill
rebuilt that one target to generation 9/9; Denia remained unchanged at 5/5.
Public current and explicit 3.7.0 transcript requests returned HTTP 200, including
the exit transition. Fourteen cinematic occurrences still lack published
sequence media: verified graph links must not be reported as completed playback.
The projection consumer was still on 15c at this checkpoint. Its subsequent
authorized graceful replacement completed at 22:31 UTC on b024, without a bulk
dirty mark; the new builder retains the exit field on future refreshes. Evidence:
`scratch/current-twoquest-postdeploy/report.md` and
`scratch/B024-BACKEND-ROLLOUT.md`.

## Persisted source and queued sequence coverage, 22:22–22:33 UTC

Bounded persistence checks retained all 517 and 654 dialogue identities, with
their exact baseline SHA256 values and fresh release-21 source identity matches.
Quest 4688653 remained at generation 9/9 and Denia at 5/5. The thirteen repaired
states still matched fifteen source edges and fifteen revisions. A repeated full
quest-walk canary hit its eight-second statement budget under load; the successful
persistence check instead used exact projection rows and known source-record IDs.

Dungeon 1557 is resolved as `dungeon_exit`, not another battle: quest node
`114000031_94` retains the explicit exit requirement, with instance source record
64689830, row 1310, release 21, mapping to map 1555. The exit has no encounter tree.
The original boss at node `114000031_231` retains LevelPlays 114000004 and 114750000.
Public current and explicit 3.7.0 responses exposed this verified transition.

At 22:24 UTC, all fourteen owners lacking published sequence media already had
queued work in **existing run 26922**. Each complete frozen presentation equalled
the current `flow_presentation(raw)` from its exact release-21 source record;
presentation hashes, recipe 4 and explicit SequenceDataAsset cues matched.
The request pins client 3.7.1 HD and download
`1f0a5f240f08d1ef1c21a658d015eb2504abcda835d85b214cb1ec789538c812`.
The owner/source/sequence matrix follows; every row refers to queued run 26922,
not an additional job.

| Owner | Source record | Sequence assets |
| --- | --- | --- |
| 4641592 | 64655020 | `DA_M3_2_18.DA_M3_2_18` |
| 4641594 | 64655022 | `DA_P0043.DA_P0043` |
| 4641599 | 64655029 | `DA_M3_2_19.DA_M3_2_19`, `DA_M0333.DA_M0333` |
| 4641602 | 64652969 | `DA_M3_2_29.DA_M3_2_29` |
| 4641604 | 64652971 | `DA_M3_2_30.DA_M3_2_30` |
| 4648367 | 64652950 | `DA_M3_2_20.DA_M3_2_20` |
| 4648369 | 64652952 | `DA_M0334.DA_M0334` |
| 4648371 | 64652954 | `DA_M0335.DA_M0335` |
| 4648373 | 64652956 | `DA_M0336.DA_M0336` |
| 4648379 | 64652962 | `DA_M3_2_21.DA_M3_2_21` |
| 4648381 | 64652964 | `DA_M3_2_22.DA_M3_2_22` |
| 4648382 | 64652965 | `DA_M3_2_23.DA_M3_2_23` |
| 4648383 | 64652966 | `DA_M3_2_24.DA_M3_2_24` |
| 4649793 | 64655025 | `DA_P0044.DA_P0044`, `DA_LS_Piao_02.DA_LS_Piao_02` |

The older request omits `sequence_media_schema`. Current bundle extraction follows
its retained sequence cues, and fragment publication validates the actual archive
and emits media schema 1. Runtime playback checks that published media schema,
exact owner/release/client/presentation proof and fragment references; it does not
require the request flag. This is distinct from future resolver cache-reuse checks.
The running b024 image consumer includes this extraction/publication path.

No owner lacked equivalent queued discovery work, so no thirteen-owner backfill,
duplicate enqueue, replay, split, cancellation or mutation of run 26922 was made.
**Queued coverage is not completed media:** extraction can still report partial
or unsupported controls. Published fragments, URLs and runtime grouping require
post-run verification; no whole cinematic is asserted.

Exact receipts: `scratch/current-twoquest-postdeploy/persistence-bounded.json`,
`links13-persistence.json`, `sequence-queued-coverage.json` and
`sequence-queued-coverage.csv` in the same directory. The latter receipt records
all fourteen source-record IDs, full presentation hashes and exact asset paths.
