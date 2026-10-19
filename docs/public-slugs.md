# Public slugs

Public addresses use a stored slug such as `/items/18423-astrite`. The prefix is
the complete graph node ID, not the game's item ID and not a truncated hash.
An item with canonical identity `item:3` therefore need not have the prefix `3`.
Graph foreign keys, canonical keys, source IDs, map filters and game IDs retain
their existing meaning. Clients use `slug`/`href` from the API, falling back to
the previous system address until a name is available.

## Lifecycle and compatibility

- English primary name documents assign slugs to characters, items, locations,
  quests and speakers as part of the same import transaction. Description and
  nickname documents cannot become the name. Named story scenes/events, game
  events and notices assign their addresses when their title is inserted.
- A title is normalized to lowercase ASCII words, stripping markup and accents;
  the name portion is capped at 180 characters. Non-ASCII-only titles use
  `entry`. The full identity prefix makes duplicate titles unambiguous.
- Once assigned, a slug is retained when names, translations or game versions
  change. Records without a resolved English primary name retain the system URL.
- Graph slugs have a unique index on `(type_id, slug)`; events and notices have
  separate unique slug indexes. Generated slugs include globally unique IDs.
- All detail API routes accept their previous keys as well as exact stored
  slugs. A wrong name with the correct numeric prefix is **not** accepted.
  Numeric quest routes still resolve the game's quest ID, not a graph ID.
- Web detail routes redirect legacy addresses to the canonical slug with HTTP
  308, preserving repeated query parameters, locale, version and dialogue anchors.
  Slug pages return details and canonical metadata in the initial server HTML.
- Shared response projections attach addresses to nested graph and quest
  references in bounded bulk queries. Raw source/provenance data is excluded.
  Published event rewards and map marker item references expose item addresses.
  Map location/marker IDs and filter parameters are not public detail slugs.

## Deployment and backfill

1. Apply Alembic migration `0015_public_slugs` using the normal online migration
   command. It adds nullable columns, indexes, name triggers and the slug function;
   it does **not** update the existing catalog. The graph unique index is built
   concurrently, so this migration must not run inside an externally imposed
   transaction. Short DDL uses a five-second lock timeout.
2. Deploy API and workers with slug resolution before deploying the web changes.
3. Preview the count, then start the backfill separately from application startup:

   ```sh
   docker exec solaris-atlas-api-1 python -m wuwa_story.ingestion.slug_backfill --dry-run
   docker exec -d solaris-atlas-api-1 sh -c 'python -m wuwa_story.ingestion.slug_backfill --workers 2 --batch-size 250 --pause 0.1 > /tmp/slug-backfill.log 2>&1'
   docker exec solaris-atlas-api-1 tail -n 20 /tmp/slug-backfill.log
   ```

   The command is shipped in the server package and uses the container's database
   configuration. No credentials belong in shell arguments or this document.
4. Run `--dry-run` again when the log prints final totals. Named eligible rows
   should reach zero. Unnamed records intentionally remain on their old addresses.
5. Verify a catalog link, an old bookmarked URL, a quest transcript with
   `game_version`/`locale`/`line`, a character dialogue search, event rewards and
   a map item's detail link. Check the canonical metadata and sitemap.

Each worker owns its own database session. Each batch locks at most the configured
number of eligible rows using `FOR UPDATE SKIP LOCKED`, updates only null slugs,
commits, and pauses. Defaults are two workers and 250 rows; limits are eight workers
and 2,000 rows. Each transaction has a 500 ms lock timeout and a 15-second statement
timeout. Progress is emitted as JSON per batch. Transient lock contention, deadlocks and serialization failures
retry up to five times with bounded backoff; statement timeouts stop the process
instead of repeatedly running an expensive query. Concurrent runs do not overwrite
existing slugs. Stop/restart is safe: committed batches remain valid and a later
run picks up unfinished or previously locked records. There is no need to reimport
game data. Existing transactional cache revision triggers invalidate API results
when slug updates commit.

An interrupted concurrent index build can leave an invalid index. Inspect
`pg_index.indisvalid` for `graph.uq_node_type_slug`; drop that invalid index
concurrently before retrying the migration. Do not drop a valid deployed index.

## Rollback

The safest application rollback retains the additive schema and slug resolver.
After users receive canonical redirects, published slug URLs must remain resolvable;
do not restore an API version that understands only canonical keys. A migration
downgrade removes event/notice slug columns, their indexes and assignment triggers,
but retains existing `graph.node.slug` values. Back up public addresses before any
destructive downgrade. Do not reset slugs to null or regenerate them from new names.
