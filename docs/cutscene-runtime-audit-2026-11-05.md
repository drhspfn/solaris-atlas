# Cutscene runtime evidence

## Scope of evidence

The inspected JavaScript and seven Wwise banks came from the locally installed
client. Their byte identity with the production 3.7.1 client has not been proven.
The retained extraction receipts include tool identity and file checksums in
`scratch/ambiguous-voice-hirc`. Do not turn local selector observations into
hard-coded production overrides.

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

## Current-release runtime check, 2026-11-05 21:12 UTC

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
The quest condition for dungeon 1557 had no matching LevelPlayData membership in
that snapshot and remained unresolved. Neither case should be hidden by assigning
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
