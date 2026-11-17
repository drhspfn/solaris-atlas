# Recurring event cycle investigation (2026-11-17)

Read-only production queries and the 3.8.0 client catalog establish these
identities. No production import, refresh, or data repair was run.

| Mode | Client identity | Production event ID | Evidence |
| --- | --- | --- | --- |
| Endstate Matrix | `activity:100770002`, type 77 | 851 | `BinData/activity/activity.json`, row Id 100770002 |
| Whimpering Wastes | `activity:100390001`, type 39 | 853 | `BinData/activity/activity.json`, row Id 100390001 and English title |
| Hazard Revisited | `activity:100300002`, type 21 | 856 | `BinData/activity/activity.json`, row Id 100300002 |

## What is stored

The latest dated Matrix occurrence is `10077000206`, version 3.7, phase 6.
Its Europe interval is 2026-10-07 03:00 to 2026-11-11 19:59:59.999 UTC.
The client 3.8.0 import created a separate undated Europe occurrence, id 5145,
`client:3.8.0`. The old occurrence remains history, not evidence that phase 6
continues. The official archived notice 50522 says the second **challenge
cycle** spans versions 3.5–3.8 and its constituent **phases** reset. The client
3.8.0 `BinData/NewTower/newtowerseason.json` row 2 independently names S2 and
version bounds 3.5–3.8; it does not give phase timestamps.
The official 3.8 patch notice 50649 states that the second challenge cycle ends
at 2026-12-21 19:59 UTC; it does not establish the start of phase 7.

Whimpering Wastes' dated Europe source occurrence `10039000102` starts
2025-03-17 03:00 UTC, with `season.start=2` and `cycle.weeks=4`, and no end or
version. Europe also has undated client occurrences for 3.7 and 3.8. The 3.8
client `BinData/ShipTower/slashandtowerseason.json` contains season IDs through
25, and `slashandtowercfg.json` has levels for season 25; neither proves which
season is active on a given server or its schedule. A direct join from these
ShipTower rows to activity 100390001 has not been established. The 2025 recurrence is
historical community-schedule data, not proof that later server schedules have
never changed.

At community archive commit `23b472a98235dcf64b33d9805563abf85c9609d3`,
`events.json` row `10077000207` names the exact Matrix `sourceId=100770002`,
version 3.8, phase 7, Europe start 2026-11-19 03:00 UTC and shared end
2026-12-21 19:59:59.999 UTC. This is a useful candidate, not a client-authored
schedule. The supported client-event import deliberately does not invoke that
community timetable. Running the legacy whole-archive importer also does not
automatically target the migrated client event: it indexes events by the former
community `source`, whereas the migrated identity is `wuwa-game-client` with
`legacy_source_id=3:100770002`. Importing it blindly could create a second
event identity instead of updating event 851.

## Projection and reader

The production current event projection was pending: scope generation 108977,
published generation 11964, last published 2026-11-12 16:51:39 UTC. Matrix
entity 851 had requested generation 946, processed 330; Whimpering Wastes 853
had 394/394. The Europe projected Matrix calendar contains phase 6 and the
undated 3.8 placeholder, but no dated 3.8 phase. Projection lag is real but
cannot create a missing source occurrence.

The shared `visible_occurrences` policy suppresses an undated client placeholder
when any dated occurrence has the same client signature, without comparing
versions. An older dated occurrence can therefore suppress a newer undated
catalog occurrence. Both source and projection paths use this policy. Any fix
must preserve old runs and handle historical version filtering; simply relabeling
phase 6 as 3.8 would be incorrect.

## Targeted import

`python -m wuwa_story.ingestion.recurring_events` reports missing schedules for
existing recurring client events, using the existing community timetable source
and a pinned commit. Add `--apply` to insert or update them. It does not create
events or delete history. Explicit corrections to the same trusted upstream
phase update that occurrence in place, including legacy keys with an `@start`
suffix. Ambiguous matches and notice-backed schedules are left untouched. Rows
retain the community source, commit, source URL, original times and version.
Existing recurrence definitions, including the
Whimpering Wastes four-week cadence, are retained. The operator must refresh the
affected event projections after applying. The standard client catalog import
continues to use client data only.

The official Notice job also runs this separate community refresh after the
official data and images have been committed. A community fetch or write failure
marks the Notice job partial with a `recurring_schedule_failed` report entry;
retrying the Notice job retries the community refresh without rolling back the
official import. No player credentials are used.

A local dry run against read-only production records for event IDs 851, 853
and 856 and pinned revision `23b472a98235dcf64b33d9805563abf85c9609d3`
proposed exactly three occurrences: Matrix phase 7, one per server. It proposed
none for Whimpering Wastes or Hazard Revisited because their stored recurrence
identities already exist. No production write was made.

The calendar retains a newer undated recurring client version when the matching
dated run belongs to another version. Banner placeholders retain their existing
cross-version deduplication. Do not derive Matrix phase dates from its
multi-version season or infer a live ShipTower phase from the highest client
season ID; confirm a current server cadence before extrapolating the 2025
Whimpering Wastes schedule.
