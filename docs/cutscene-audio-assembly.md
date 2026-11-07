# Cutscene audio assembly: bounded source audit

This audit covers the audio-facing branches in the pinned 3.7.1 client files
`VideoView`, `VideoConfig`, `VideoDefine` (`VideoUtils`), `VideoLauncher`,
`FlowActionPlayMovie`, and `PlotAudioModel`. It enumerates 16 mechanisms and all
eight `VideoView` calls to AudioSystem: two posts, three stop actions, two state
changes, and one seek. It is not a complete audit of the game or its native C++
runtime. Source snippets and game binaries are not included here.

The complete native catalog smoke scanned 347,962 records: 21,149 FlowState,
26,067 QuestNodeData, 85,911 LevelPlayNodeData and 214,835 level entities.
All four sources completed without record errors in 38.6 seconds. Empty movie
names are native callback-only no-ops; ordinary string parameters and structured
Name/Type properties do not abort traversal. Malformed records retain their
diagnostics while later records continue to be examined.

## Finite mechanism matrix

| # | Mechanism | Verified behavior and implementation boundary |
| --- | --- | --- |
| 1 | Embedded media audio | `CgFile` loads a native media source. Preserve its embedded mix; native media-track routing and independent separation are not established by these JS files. |
| 2 | Video gender | Exact VideoData gender selection; player gender 2 maps to 0. A shared video does not establish a Wwise Rover selector. |
| 3 | VideoSound precedence | The shared gender-2 list takes precedence; gender-specific rows are a fallback. The lists are not additive. |
| 4 | VideoSound start/end | Source seconds determine immediate/pending starts and explicit handle stops. These endpoints belong to VideoSound handles. |
| 5 | VideoSound synchronization | The duration callback can seek against the media-player clock after a 200 ms startup delay. Offline preparation does not invent device latency. |
| 6 | Caption language/timing | Select the whole localized list when nonempty, otherwise the whole default list. Caption frames use 33.33 ms; movie frame-event `Second` already means seconds. |
| 7 | Caption external voice | Only enabled `IsPlaySubtitleVoice` rows request exact PlotAudio external media. Unresolved runtime filename substitutions remain unsupported. Caption text duration is not a voice stop. |
| 8 | Caption voice cleanup | Cleanup stops stored subtitle voice handles. It does not prove the lifetime of a separate frame-posted event. |
| 9 | Frame-event registration | `Mp4FrameEvents` passes from PlayMovie to VideoLauncher. Preserve raw actions and source identity; unverified MovieEvent aliases stay unresolved. |
| 10 | Frame-event dispatch | Dispatch compares source `Second` with the movie clock. Stable descending sort followed by pop reverses equal-time frame groups; actions within a group retain their order. Conditional, spatial and unknown actions are not flattened. |
| 11 | Global/MusicSubtitle posts | Resolve the exact cooked event, localized banks and source media. Preserve mode/group metadata. The native handler body and its stop policy were not recovered; mode alone does not establish an audio role. |
| 12 | Independent roles | Follow HIRC source parents, inherited output buses and Init bus RTPC controls matching `Volume_Music`, `Volume_Voice`, and `Volume_SFX` identities. Preserve native top-level TXTP layer commands. Unknown/conflicting roles and nested groups are rejected. Localization and filenames are not role classifiers. |
| 13 | Initial states/selectors | Support proven initial state, language and Rover selections. The frame lane also retains a state before Play when the event/Init HIRC audio graph has no consumer. Changing/after-Play states, unsupported consumers and unresolved random/switch selections remain unsupported. |
| 14 | Music before/during/after | Preserve source actions and clocks. Multiple frame events and cross-event Stop/SetState/RTPC or existing music phase require a shared running timeline; they are not rendered as independent approximations. |
| 15 | End/seamless lifecycle | VideoView changes the plot-video state and conditionally cleans VideoSound/subtitle handles. Clearing frame-event registration does not itself prove that its previously posted Wwise event stopped. |
| 16 | Application pause/resume | JS handles media/timers and delegates global background audio handling to native C++. Offline preparation does not reproduce application interruptions. |

The separate launcher catalog discovers distinct source contexts; alternative
contexts must not be merged. Missing catalog coverage and unresolved parser
diagnostics must remain visible to the job/publication layer.

## M0374 native smoke evidence

CG rows 444/445 share
`/Game/Aki/Sequence/LevelA_Seq/Main/03_5/M0374/M0374.M0374`.
The wrapper `剧情_3_5_玄方城_决战_4_3_5` posts
`/Game/Aki/WwiseAudio/Events/play_all_sound_m0374.play_all_sound_m0374`
at movie second 0, action 5000198. Source records 54013229 (release 19) and
64656466 (release 21) establish this binding. VideoSound has no M0374 rows;
all four caption voice flags are false. The missing audio is an independent
frame-event lane.

The cooked event identity is 4102954561. EN/JA localized banks and source media
were extracted from the verified 3.7.1 base/voice plans, without a broad audio
export. Native routes distinguish the selected layers:

| Media ID | Role | Native routing evidence |
| --- | --- | --- |
| 1840697 | Music | Music track 740684015, segment 915569492, playlist 364911269; output bus 156558589 reaches bus 3991942870 and RTPC 3891337659, matching `Volume_Music`. |
| 379912756 (EN), 741257113 (JA) | Voice | Voice source path reaches bus 254601957 with RTPC 3676256460, matching `Volume_Voice`. |
| 410668537 | Effects | Sound 850196698 through parent 43541126 reaches bus 2856493138 with RTPC 3673881719, matching `Volume_SFX`. |

The public `prepare_frame_audio` canary produced nonzero stereo 48 kHz PCM:
shared effects 169.7 s, EN voice 169.7 s, JA voice 169.7 s, and shared music
196.99275 s. Shared EN/JA music/effects were collapsed only after equal PCM
hashes were established. There is no authored Rover switch in these banks;
the result has `rover=None`.

Summing the independent EN roles matched the original native three-layer TXTP
render exactly over 18,911,304 samples: maximum 16-bit delta 0, no clipping.
Native automation and volume commands were retained. This proves the rendered
event subset, not full game-engine playback or deployment.

The natural music tail is retained. A movie duration of 169.7 s does not prove
a native stop for a frame-posted event. Any assembly projection onto the video
must preserve that distinction. KO/ZH banks are absent from the local canary
archive view and remain explicit diagnostics; all-four-language completion is
not claimed.

Local evidence receipts are under
`scratch/m0374-audio-orchestration-20261106/`: `generic-prepare-proof.json`,
`native-bus-role-proof.json`, `role-sum-golden-proof.json`, and
`source-audit-matrix.json`. The source archive contains 28 entries, including
Init plus EN/JA banks, five WEMs, original event packages, and JSON/XML/TXTP
evidence. Binaries remain outside tracked documentation.

## Operational failure policy

Unsupported source data can produce partial audio diagnostics while retaining
video. Infrastructure `OSError`/`RuntimeError`, dependency deferrals, timeouts,
OOM process exits and allocation failures propagate to the job layer.
The existing broker retry classifier includes temporary disk/memory/file-handle
resource errors; missing authored files and permission failures are not blindly
retried. Operational failures must not be published as terminal missing audio.

## Movie songs and dialogue continuations (2026-11-07)

Verified client 3.7.1 source paths for the 3.7.0 story snapshot:

- M0389 posts `play_all_sound_m0389_music_lyric` through `Mp4FrameEvents`
  at movie second zero. Its initial Stop clears the previously running game
  music before the new Play. In a fresh isolated render it has no playing
  instance to stop; the original control timeline remains retained as evidence.
  Stops after a Play or at nonzero times still require a shared runtime.
- M0390 posts `play_all_sound_m0390_lyric` at movie second zero. Its localized
  VideoSound also sets group 2267264624 to 4278155591. The native consumer in
  `play_story_music_3_7_boss_tianyansuxin` selects node 731374590 and its authored
  intro/loop. The companion bank and every selected media source are retained.
  The prior combat phase is reconstructed from its fixed entry, not presented
  as an exact reproduction of the combat-to-video crossfade.
- M3_13_03_1 follows ShowTalk posting `play_all_sound_m3_13_03_lyric`, at
  TalkItemId 34 / Offset -1 in the same native flow. Retain that explicit
  continuation, including its source position, rather than treating a frameless
  PlayMovie as proof of no music. Standalone playback reconstructs its start at
  movie time zero: player choice delays and the preceding sequence elapsed time
  are not reproduced. The native TXTP `group = -S1 #p 0.01` and its source
  volume envelope stay intact; homogeneous role groups require no flattening.

Explicitly TEST-labelled native flows remain in the source report but do not
replace or conflict with production launcher audio. Other conflicting runtime
contexts remain unresolved. These paths do not infer songs from filenames or
merge mutually exclusive gameplay branches.

### Authored song precedence (M0390 overlap correction)

An explicitly posted song at movie time zero takes precedence over a fixed-start
persistent-background reconstruction. Both may contain the same song at
different phases and must not play simultaneously. The consumer bank/state
evidence is retained for localized-event validation; the approximate background
WAV is not added as a second playable stem. Other authored layers are preserved.
A song carried from dialogue also takes precedence, with its elapsed-time
limitation still recorded explicitly.

The two published M0390 bundles retain song file 602271 and remove reconstructed
phase file 603174 from playback. Original manifests and sources remain available
for audit. Reimport recipes now include the fallback policy in their identity.
