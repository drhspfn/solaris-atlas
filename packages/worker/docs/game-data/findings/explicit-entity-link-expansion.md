# Explicit entity link expansion

The 3.6.0 compiler snapshot now scans inventory-listed BinData tables for exact
`ItemId`, `AreaId`, `RoleId`, `CharacterId`, `NpcId`, and `SpeakerId` fields.
Each discovered row is retained as a `source_reference`; a graph edge is emitted
only when the value exactly resolves in the corresponding compiled namespace.
The edge retains the table path, raw JSON path, raw value, and
`exact_namespace_id` resolution basis. Definition tables are excluded because
their own IDs define canonical entities rather than references.

QuestNodeData/QuestData-specific links remain handled by their dedicated
normalizers. No ID is matched across namespaces by numeric similarity. Missing
targets remain diagnostics, and all original raw records remain available.

The item table also contains `DecomposeInfo: [{Key, Value}]`. `Key` values in the
observed records match `ItemInfo.Id` exactly; those entries are normalized as
`item --decomposes_into--> item`, with `Value` retained as quantity. The 3.6.0
snapshot contains 23 such edges. `CompositeItem` is retained raw: its observed
two-element values do not establish an unambiguous direction or relation name,
so no edge is inferred from that field. `ItemAccess` values are not treated as
item IDs because they reference a separate access/config namespace.

The snapshot contains 573 explicit area references, 5,146 item references, and
7,965 character references. Counts are edge counts, not counts of unique target
entities. These links describe authored config references; they do not by
themselves prove that a character speaks in a quest or that an item is obtained
by the player.

## Search and API

The lexical index includes localized area names and item descriptions. Search
category filtering uses the graph node type, so `category=area` includes all
search document subtypes for an area and `category=item` includes its name and
description. `/nodes/{canonical_key}/related` returns paginated explicit graph
neighbors and supports `direction`, `relation`, and `category` filters.

Character-to-speaker resolution remains limited to explicit crosswalk evidence.
Text mentions can be found through dialogue search, but are not represented as
speaker identity or participation edges.
