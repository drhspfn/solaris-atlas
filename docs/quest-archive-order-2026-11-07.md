# Quest scene ordering recovery

The release-21 Simulacrum of the Heart page used technical flow-key order when
its snapshot node records did not match the retained client tree. Quest
886000002 has 89 snapshot nodes versus 88 in the retained 3.7.0/3.7.1 trees;
LevelPlay 683000006 has 110 versus 87. The exact-tree validation remains intact.

The same snapshot's `BinData/PlotHandBook/plothandbookconfig.json`, record
64312991, row 94, supplies an explicit archive list spanning nested scenarios.
When an exact tree is unavailable, the reader uses its distinct `review_position`
instead of lexical state names. This is archive presentation order, not a claim
that conditional branches execute in a single runtime timeline. Repeated states
and conflicting handbook records receive no invented unique position. Movie
caption states inherit an unambiguous owning movie's archive slot.

The production read-only pilot returned:

| Scene | Archive slot / action |
| --- | --- |
| M3_13_03_1 | 33 / 5 |
| M0385 | 48 / 6 |
| M0388 | 440 / 1 |
| M0389 | 446 / 1 |
| M0390 | 451 / 1 |
| Illustrated dialogue, flow ending `_2_18` | 452 |

M0391 has an explicit owner in quest 168000003. Its source `PreQuest` requirement
names 886000002, so the related player links to that later quest. It is not
assigned a false position inside its prerequisite quest. The user's corrected
reference agrees that M3_13_03_1 is the opening scene, before M0385.

The illustrated dialogue references `DA_M3_13_41`; native event bytecode opens
`UiItem_CG3_7_shaungxinjieheyi`. Its prefab contains multiple Spine rigs and its
flow contains 14 voiced lines. The packed atlas is not a finished illustration.
Preserve the composition/control sources and validate render support before
publishing an animated preview or a static poster.

## Rebuild

After deploying the matching server/worker images, audit without writes:

```sh
python -m wuwa_story.ingestion.review_order_backfill --all-releases
```

The audited 21 releases contain 1,109 quest-version scopes with usable archive
lists, including 96 in release 21. Apply in a detached process with durable logs:

```sh
python -m wuwa_story.ingestion.review_order_backfill --all-releases --repair-links --apply
```

Each release commits independently. Existing graph evidence is reconciled by
the existing snapshot-scoped repair; dirty projection scopes coalesce. The
command also atomically rebuilds the cutscene catalog order. It does not enqueue
video/audio renders or remove published media. Errors identify the release and
retain traceback frames; rerun failed release IDs explicitly.

Run the updated projection worker before consuming these scopes. Refresh the
pilot current and explicit-version page first and verify its media order,
dialogue order, continuation link and HTTP status. Existing media workers must
drain gracefully; an application deployment does not authorize cancelling their
active imports. No schema migration is required. Rolling back code and
rebuilding affected projections restores the previous display order without
discarding source data or published assets.
