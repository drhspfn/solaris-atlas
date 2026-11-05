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
