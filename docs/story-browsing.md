# Story browsing API

The API exposes source-backed browsing primitives over an imported release. It is
intended to support a character page, quest page, category filters, and dialogue
search. It does not infer relationships or claim that authored order is a single
runtime playthrough.

## Search and categories

```bash
curl 'http://localhost:8000/categories'
curl 'http://localhost:8000/search?q=denia&category=character&locale=en&sort_by=relevance&limit=20'
curl 'http://localhost:8000/search?q=Hiyuki&scope=dialogue&locale=en&game_version=3.6.0'
curl 'http://localhost:8000/dialogue/search?q=surprise&character=character%3A1211&locale=en&game_version=3.6.0'
```

`/search` supports repeatable category filters, locale, relevance/name ordering,
direction, offset, and paging. UI categories include `character`, `item`,
`location`, and `quest`; `location` maps to raw `area` nodes. Set
`scope=dialogue` to use the same route for localized dialogue text, optionally
filtered by character and quest. `/dialogue/search` remains available for the
full detailed result shape. `/categories` returns both raw graph node types and
normalized `browse_categories` with counts.

For the initial UI, use `/characters/{key}/profile`,
`/items/{key}/profile`, `/locations/{key}/profile`, and
`/quests/{game_quest_id}/profile`. The detailed query parameters and response
fields are documented in [frontend-api-v1.md](frontend-api-v1.md).

Use `/nodes/{canonical_key}/related` to page through meaningful graph neighbors.
Raw `source_reference` rows are summarized by source table by default, so the
main response does not bury character, quest, item, or area links in hundreds of
configuration records. Set `include_evidence=true` or pass
`category=source_reference` to expand those records. Filter an evidence group
with `source_file`, then open an individual record through
`/nodes/{source_reference_key}/source-record`. The response includes the raw
upstream row and source path. `direction=in|out|both`, `relation`, `category`,
`limit`, and `offset` are also supported.

```bash
curl 'http://localhost:8000/nodes/character%3A1211/related?direction=both&limit=50'
curl 'http://localhost:8000/nodes/character%3A1211/related?category=source_reference&source_file=BinData/favor/favorword.json&limit=20'
curl 'http://localhost:8000/nodes/source_reference%3Afavor/favorword.json:3363/source-record'
curl 'http://localhost:8000/nodes/item%3A41400014/related?relation=decomposes_into'
```

## Character profile

```bash
curl 'http://localhost:8000/characters/character%3A1211/profile?locale=en&game_version=3.6.0'
```

The profile returns the character identity, known aliases, deterministic
speaker-to-character links, quests reached through speaker → dialogue → flow
state → quest evidence, explicitly stored graph links with provenance, and
speaker records that share an authored flow state. It also returns a compact
`connection_summary` and groups direct raw config references under
`source_evidence`, with human-readable table labels. Raw config rows are not
listed as character relationships. If `narrative_status` is
`no_confirmed_speaker_crosswalk`, the current dataset has no deterministic
speaker-to-character mapping for that character; name mentions in dialogue do
not count as proof that the character spoke. Shared-state co-presence is not
presented as a personal relationship. Unresolved speakers remain visible
without being promoted to characters.

Use `/dialogue/search?q=Hiyuki` to find text that mentions her. Search results
show the actual speaker, so this is distinct from dialogue attributed to
Hiyuki. For example, the 3.6.0 snapshot has Lucilla saying “No need for all the
formality, Hiyuki.” That search hit alone does not prove Hiyuki is present in
the scene or identify her speaker ID.

For the imported 3.6.0 snapshot, Denia resolves to `character:1211`, has two
exact-join speaker links, and appears in four quest records:

- `119000000` — Beneath a Melting Night Sky
- `121000040` — Starlights from Yesterdays
- `165800000` — Rabbit Reflected in Shades
- `168800009` — Gold Suspended in Shadows

## Quest transcript

```bash
# Denia's lines in the main quest
curl 'http://localhost:8000/quests/119000000/transcript?character=character%3A1211&locale=en&game_version=3.6.0'

# Full authored dialogue transcript, including other speakers
curl 'http://localhost:8000/quests/119000000/transcript?locale=en&game_version=3.6.0'
```

The response includes scene order, authored state/action/talk ordering, localized
text and localization keys, speaker identities, raw source records, and player
choices attached to dialogue lines. `character` and `q` filters can narrow the
transcript. `limit` and `offset` support paging. A filtered transcript answers
“what did Denia say”; omitting the character filter returns surrounding
conversation so the user can read what other characters said in response.

For Denia in quest `119000000`, the database returns 38 scenes and 226 dialogue
lines; 41 choice records are attached across those lines. Authored order does not
imply that all branches occur in one playthrough. `explicit_area_references`
contains only area links present in imported quest-node graph evidence; an empty
list means no such explicit link was imported for that quest.

## Current boundary

These are browse/search API endpoints, not a visual web client. They provide the
backend data contract for a later UI with category navigation, character cards,
related-entity panels, quest transcripts, and in-transcript search. Character
crosswalk coverage is still partial; unmapped speaker records are exposed as
speakers and are not silently assigned to a character.
