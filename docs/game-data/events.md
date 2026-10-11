# Event presentation from the game client

The public archive supplies dated schedules and source IDs. It does not supply
reliable localized event names. The event import now queues the following tasks
on the event media queue:

1. `prepare`: select/download the client.
2. `event_catalog`: read ConfigDB and reconcile exact archive IDs.
3. `event_images`: extract and publish the reconciled texture paths.

Events without an archive image are included. Images wait for the catalog, so
they use the resolved client backgrounds rather than a stale list of paths.
Task identities include the archive revision; updated archives get new tasks.
Repeating the same import preserves client presentation and reuses completed work.

## Verified client fields (3.7)

| Table | Field | Meaning |
| --- | --- | --- |
| Activity | 20 | Title localization key |
| Activity | 23 | Description localization key |
| Activity | 24 | Background texture |
| GachaViewInfo | 1 | Convene type |
| GachaViewInfo | 10 | Background texture |
| GachaViewInfo | 16 | Featured character/weapon IDs |

Both `lang_multi_text_1sthalf.db` and `lang_multi_text.db` are read for each
locale. Activity localization keys must be read from the row: they are not
necessarily derived from its ID. Convene names use the localized featured
character or weapon name, not a romanized texture filename. Marketing campaign
titles and missing descriptions are not invented.

Specific archive activity previews are retained. Missing activity images and
convene thumbnails use client backgrounds. Event textures are exported
individually, avoiding conversion of an entire artwork directory.

Presentation and provenance are stored in existing `source_data.client` JSON;
no schema migration is required. Current-version occurrences also retain their
own presentation. `/api/events?locale=en` supports a locale parameter with
English fallback. Historical IDs absent from this client retain archive data.

## Diagnostics and verification

The catalog task's import report lists each matched ID and title key. Missing
rows and missing localization are distinguishable by `config_present`; the
expected localization key is included in `nearby`. Missing historical rows do
not block image processing for events that were resolved.

Verified against the server's exported 3.7 ConfigDB: all 25 archived 3.7 events
resolved English names, including Chord Cleansing, Cubie Wars, Lynae, and Lucilla.
The actual Lucilla convene background and Chord Cleansing background were also
successfully exported and decoded in the server's Linux worker container.
After deploying, re-import the event archive to enqueue the new catalog stage.
Check that it completes before checking the dependent image tasks.
