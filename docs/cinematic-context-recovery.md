# Possible cinematic connections

The compiler searches nested explicit PlayFlow calls and exact authored Flow.Id / Flow.States membership. Unknown targets do not stop sibling branches; visited states stop cycles. Shared names, translated text and consecutive state numbers are insufficient evidence.

Recovered connections use `references_scenario_flow_state` with recipe `authored-flow-context-v1`, confidence `possible`, source anchors and `runtime_order_confirmed=false`. Competing quest candidates remain separate. These links are research context, never confirmed quest ownership or a chronological insertion. The existing quest media, dialogue context and analysis inventory consume them as inferred connections.

Rebuild retained links without downloading game assets or reimporting media:

```sh
python -m wuwa_story.ingestion.dungeon_backfill --first 3.7.0 --last 3.7.0 --quest-id 121000040 --dry-run
python -m wuwa_story.ingestion.dungeon_backfill --first 3.7.0 --last 3.7.0 --quest-id 121000040
```

Review counts before applying a broader version or quest scope. Repeating the same repair is idempotent. Changed or removed proof replaces only this recipe's evidence in the selected release and quest scope. Quest, cutscene and caption dialogue read models are invalidated for current and historical views. Other recipes and confirmed or other-release evidence are preserved.

No schema migration is required. Rolling back code does not retract published evidence: remove only the selected recipe-owned edge evidence and invalidate the affected views. An engine cinematic with no complete recording remains explicitly unavailable; partial video components are not advertised as the full cinematic.
