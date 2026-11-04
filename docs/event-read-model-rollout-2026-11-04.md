# Event read-model rollout — 2026-11-04

Branch: `perf/events/read-model-latency`.

The production API had `READ_PROJECTION_KINDS=character,story_map,game_notice,location`.
Event projections were already published (`game_event/current`, published generation 1822,
632 English event entities), but requests still used the source renderer and its media,
announcement and occurrence lookups.

Enabled `game_event` in the production GitHub Environment variable, preserving the other
kinds and the existing `current`/`en` rollout boundaries. Deployment run: 41905587823.
API rollout succeeded; the projection worker was recreated and its environment also includes `game_event`.
No schema changes or source-data rewrites are necessary. Existing dirty-row publication
keeps these projections updated as event imports and asset references change.

## Reproducible validation

Run `scripts/benchmark-event-projections.py` inside the API container with its configured
server dependencies. It is read-only and never prints source payloads or credentials.
Use `--compare-source` deliberately: this performs the expensive source query once.

```sh
python /tmp/benchmark-event-projections.py --samples 3 --compare-source
```

Measured against the migrated server while client download and animation conversion ran:

- Projection reads: 0.493, 1.063, 0.601 seconds; median **0.601 seconds**.
- Source renderer: **23.052 seconds** in the same parity run.
- Initial separate source sample: 11.567 seconds; external API: 12.449 seconds.
- After rollout, the external API returned HTTP 200 in **2.238 seconds** versus
  12.449 seconds before rollout; the event page returned HTTP 200 in 3.042 seconds.
- Europe calendar: **753** event/occurrence identities; **zero missing, extra or changed**.
- Comparison normalizes datetime serialization through FastAPI before comparing complete
  occurrence objects. List ordering is compared by identity because ordering ties may differ.

These are server-side samples under changing load, not a claim about every browser's
page load. A first cold projection sample took 3.433 seconds. Source queries from other
endpoints can still saturate database I/O; those need separate measurement.

## Rollback

Remove only `game_event` from `READ_PROJECTION_KINDS` in the production GitHub Environment
and redeploy the API. This preserves all projection data and the other enabled readers.
The independent projection worker must include `game_event` in its selected kinds to
consume future dirty entries; verify its environment after rollout.
