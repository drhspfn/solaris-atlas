# Event catalog and lifecycle audit — 2026-11-17

This is a read-only production observation, not a claim that all same-name
records are duplicates. The companion [CSV](event-catalog-audit-2026-11-17.csv)
classifies every one of the 666 current client event IDs for the Europe server.
It records the exact source ID, title, kind, snapshot membership count, number
of scheduled/placeholder/notice/pool-proven occurrences, equal-title ID count,
and signature comparison counts. `same_title_unproven` does not assert a join.

One public `summary=true` export returned 1,237 rows for 666 IDs. The separate
bounded source inventory found 144 IDs with both a scheduled occurrence and a
client placeholder, and 522 with only client placeholders. Eight IDs have
notice-verified pool evidence; 431 IDs have a same-title peer without that
evidence. For Europe, 251 placeholder rows have a scheduled row under the same
event ID: 40 match at least one stored client signature, while 211 differ or
cannot be proven to match. There are 1,022 placeholders without a scheduled row.
These are row counts, not 251 proven duplicate events; do not merge them by title.
The signature comparison is an audit aid, not a source identity guarantee.

## Lily evidence

`GameEvent` 1945 is `gacha:100090`, with 3.8.0 membership. Its client row has
`ShowIdList=[1512]` (Lily), `UpList=[1102,1103,1202]` (Sanhua, Baizhi,
Chixia), and `PreviewIdList=[1105,1102,1103,1202]`; the preview list is not
the featured or official rate-up roster. Notice 36 / source 50623 links to
1945 and explicitly states Youhu, Aalto and Yangyang. The official notice has
different run evidence; the client fields must remain intact.

European occurrence 6228 is `client:3.8.0`, with no dates; 6288 is
`notice:50623@update`, ending 2026-12-03 08:59 UTC. Its start
2026-11-11 10:59:59.999 UTC is an estimate from the previous version's banner
end, not an observed update completion. Both occurrences retain the same
download ID and the same client-signature inputs. At observation, published
current projection generation 45 (updated 2026-11-17 15:14:36 UTC) had neither
`_catalog_placeholder` nor `_client_signature` for either row. The duplicate
shown there is therefore a stale-projection/missing-metadata case, not a Lily
client-signature mismatch. Its notice run has `notice_snapshot` artwork while
the catalog occurrence has `game_client` art. Refresh is an operator action,
not performed here.

The 3.8 label in `catalog_version` is not a schedule version. A bounded source
check found that Europe occurrences 2439/2448/2451/2454 (Fantasies) and
2457/2460 (Hazard Revisited) have no captured client data and no game version;
their 3.8 catalog presentation comes from the current event fallback despite
dates in 2024–2026. Lily 6288 has a stored 3.8 client snapshot and a separately
pinned notice. `catalog_provenance` now distinguishes `occurrence_snapshot`,
`current_catalog_fallback` and `unavailable`; none asserts which game version
ran the schedule. Where the exact pinned schedule notice explicitly says
“Version 3.8 update” in its Duration section, `schedule_evidence` exposes the
notice ID, revision and `update_version` as evidence. It does not set
`game_version`, establish an actual update completion time, or relabel other
unversioned historical occurrences.

The official roster slice in this branch reads an explicitly stated rate-up
roster only from a pinned notice presentation, returns it as `up_names`, and
exposes the untouched client roster separately as `client_up_names` with
`up_names_source=notice`. Ambiguous or unrecognised text is not guessed. It
changes neither client snapshots nor notice revisions. Published projections
must be rebuilt after deployment before existing materializations show it.

## Model and safe implementation boundaries

* `db/models/game_events.py` already has one `GameEvent` per `(source,
  source_id)`, `GameEventOccurrence` per `(event_id, source_occurrence_id,
  server)`, and `GameEventSnapshotMember` per `(version,event_id)`.
* `ingestion/client_events.py` writes one current client presentation on the
  event, snapshot membership separately, and creates per-server `client:`
  placeholder occurrences for the imported major/minor version.
  `ingestion/notices.py` finds or creates a separate notice occurrence and pins
  its notice revision and client presentation. This produces two rows for Lily;
  it is not necessarily two real runs. Existing signature suppression in
  `events/catalog_duplicates.py` is a compatibility read policy, not a
  substitute for source lifecycle modeling.
* Membership currently contains **no per-version presentation**. Older client
  presentation can survive only inside occurrence JSON, whereas the event
  itself holds the latest client presentation. Event 796 is concrete evidence:
  its 3.7 and 3.8 occurrence snapshots under one ID differ, and one old snapshot
  is mixed. Deleting placeholders or folding them into another run by event ID
  could lose a version capture or attach a different presentation to history.
  A durable catalog-without-run model therefore needs a versioned presentation
  reference or payload on snapshot membership, with provenance, before those
  occurrence records can be retired. That is a schema/backfill decision, not
  part of this patch.
* A safe staged importer change would first persist the exact versioned client
  capture and its source/download identity; then upsert a period by an explicit
  source identity, server and verified notice or historical schedule. Never
  infer a period from client membership, a generic title, or artwork. Preserve
  uncertain links separately. Lock and retry consistently with existing catalog
  and notice writers, preserve both original source IDs, and verify that repeated
  imports and revision updates do not duplicate or overwrite periods.
* Current notice matching allows a unique featured subject even when its client
  rate-up list differs; the notice roster is authoritative only for the pinned
  notice run, while the original client list remains separate. Reverb grouping
  requires the existing `GachaPool` proof and notice revision/window; preserve
  all selectable member IDs. Same-name activities and permanent/limited phases
  require authored identity/relationship evidence, not names. Peaks artwork and
  presentation are being handled by the separate media workstream.
* Projection publishing must invalidate the affected event on occurrence,
  notice-revision and relevant media changes. All list/detail/source/projection
  paths should consume one run presentation policy. During staged rebuilds,
  absence of proof must retain data rather than suppress it. Event 796 must
  remain distinct where captured evidence conflicts; repairing its 3.7 source
  still requires the exact original download or equivalent verified export.

Recommended regression matrix: Lily's dated and catalog rows and differing
rosters; 796's conflicting captures; complete/incomplete Reverb pools and
individual member links; same-name unrelated activities; permanent phase versus
time-limited promotion; recurring seasons and repeated imports; partial or
missing snapshots; estimated starts and later verified corrections; pinned
notice revisions, image provenance and stale/rebuilt projections. Exercise
source, projection and UI contracts, including idempotent reimports and concurrent
publication. The recurring/legacy importer and Peaks/media changes belong to
their respective workstreams and are not modified in this branch.

## Timeline observation for main

`calendar.ts` filters each run to the 42-day interval before allocating lanes,
but assigns lane height across that entire interval. The horizontally visible
scroll viewport can contain fewer days, so lanes or section headings whose bars
are elsewhere in the 42-day interval may look empty. `layoutTimeline` does omit
groups with no bars anywhere in that interval. In a bounded source query for
November 2026, Europe had 18 banner, 9 limited, 1 permanent and 1 recurring
scheduled rows intersecting the 42-day interval; only 12, 1, 1 and 0 respectively
intersected the 17–25 November subwindow. This demonstrates the possible
window/viewport distinction, not the exact cause of a screenshot without its
scroll position and rendered data. Do not remove real periods or reinterpret a
dated promotional window as a permanent-mode lifetime merely to fill space.
