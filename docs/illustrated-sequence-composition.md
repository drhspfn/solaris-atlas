# Illustrated sequence compositions

Sequence metadata revision 5 rebuilds retained revision-4 archives without a
second game export. Earlier archives still require extraction because they may
lack reachable prefab/control assets. Existing published sources remain immutable
in object storage; new publications use the normal source-pinned media pipeline.

`manifest.composition` records `schema_version: 1`, `state`, `reasons`, and
`frames`. A frame identifies its archive entry, control source, MovieScene source,
and authored state start time in seconds. `sample_time_seconds` separately records
the settled frame sample, after the transition finishes; the reading UI can show
that illustration from the state's beginning without pretending to reproduce its
transition. Entries are covered by the bundle inventory's byte
length and SHA-256. Publication verifies PNG bytes before creating
`story_sequence_illustration` references. API URLs require the same release,
owner, bundle, source provenance and frame identity; a different bundle cannot
supply missing images.

The initial compositor supports settled UI states with exact prefab actor GUID
bindings, uniform Spine transforms, and verified standard materials. It preserves
the authored canvas aspect ratio. It does not approximate unknown UI tracks,
materials, or easing during transitions. These are static illustrations, not a
claim of complete animated playback. Unsupported source controls and conversion
or render failures remain distinct diagnostics; source assets are retained.

## Pilot and backfill

The fox scene after M0390 belongs to
`flow_state:剧情_3_7_心相迷宫&后日谈_2_18`, release 21, quest 886000002.
Its revision-4 source bundle was file 603982, reference 1041539, run 29029.
It contains four rigs and authored OpenUiView, UI transition and CloseUiView
calls. A completed source import alone did not mean a composition was rendered.

Run a single source-pinned pilot before the bounded project backfill. Do not
clear queues or blindly republish pending tasks. Resume from the durable receipt;
already-current inputs are reused by the existing enqueue/idempotency checks.
Unsupported results must not be reported as ready animations.

`scripts/backfill-illustrated-sequences.py` defaults to a read-only plan and a
maximum of 50 flow owners per call. `--apply` dispatches only the audited scope,
with a durable per-release checkpoint. `--fresh-client` is an explicit separate
pass for older downloads: it records the client-version transition and requires
a full extraction from the selected prepared client instead of reusing an old
archive as new proof. Use separate checkpoints for these two source modes.

## Reading and background audio

The reading UI matches a dialogue's exact localization/voice key and MovieScene
to its illustration state. Manual advance plays the selected voice; Auto moves
on voice completion, or uses a bounded reading delay when no voice is available.
The reading delay is a convenience, not the game's authored timing.

Native LevelPlay context may supply a background music phase. The resolver binds
the flow to its native tree, preceding Play, entering state and finishing Stop;
Wwise independently verifies the state consumed by that Play and its matching
Stop target. A rendered track is marked `role: music`, with an explicit native
loop flag and `fixed_start_reconstruction` timing. It is one persistent audio
element across dialogue changes, rather than a copy of a mixed soundtrack.

The fox pilot verified the native node `683000006_32`, stage 4, one 30-second
music loop, and three rendered illustrations on Linux. Targeted Python checks
and the complete web test suite precede production publication. A normal
production job must still verify their combined publication and public URLs.

App deployment updates API/web only. Drain reviewed image-worker IDs gracefully
before replacing workers; preserve unrelated imports and infrastructure. No
database migration is required. Rollback uses the prior images; retained source
archives remain usable by either version.
