"""Thin API adapters and compatibility exports for public projection read services."""

from wuwa_story.read_models.context import should_route_to_projection as should_route_to_projection
from wuwa_story.read_models.readers import catalog, cutscenes, entities, events, search

supports_node_projection = entities.supports_node_projection
ENTITY_PROJECTION_KINDS = tuple(kind for kind in entities.ENTITY_READ_MODELS if kind != "story_map")


def should_route_node_to_projection(identity, snapshot=None, *, settings=None):
    if not supports_node_projection(identity):
        return False
    prefix = str(identity).partition(":")[0]
    return should_route_to_projection(
        entities._NODE_KIND_ALIASES.get(prefix, prefix), snapshot, settings=settings
    )


def should_route_search_to_projection(
    category=None, scope="entities", snapshot=None, character=None, *, settings=None
):
    kinds = (
        ["dialogue"]
        if scope == "dialogue" or category and set(category) == {"dialogue"}
        else (category or [kind for kind in ENTITY_PROJECTION_KINDS if kind != "dialogue"])
    )
    if snapshot and snapshot != "current" and not category and scope != "dialogue":
        kinds = [kind for kind in kinds if kind not in {"location", "speaker", "game_notice"}]
    kinds = [
        entities._NODE_KIND_ALIASES.get(kind, entities.KIND_ALIASES.get(kind, kind))
        for kind in kinds
    ]
    if character:
        kinds.append("character")
    return should_route_to_projection(kinds, snapshot, settings=settings)


catalog_entry = catalog.catalog_entry
catalog_page = catalog.catalog_page
category_counts = catalog.category_counts

cutscene_catalog = cutscenes.cutscene_catalog
cutscene_detail = cutscenes.cutscene_detail

_NODE_KIND_ALIASES = entities._NODE_KIND_ALIASES
_NODE_KINDS = entities._NODE_KINDS
KIND_ALIASES = entities.KIND_ALIASES
ProjectedPayload = entities.ProjectedPayload
_coverage = entities._coverage
_identity_statement = entities._identity_statement
_kind = entities._kind
_locale_id = entities._locale_id
_missing = entities._missing
_public = entities._public
_ready = entities._ready
_ready_card = entities._ready_card
_require_scope = entities._require_scope
_resolve = entities._resolve
_scope_entries = entities._scope_entries
_unready = entities._unready
archive_page = entities.archive_page
node_projection = entities.node_projection
node_related_page = entities.node_related_page
node_section = entities.node_section
page_payload = entities.page_payload
profile_page = entities.profile_page
projection_entry = entities.projection_entry
quest_section = entities.quest_section
story_map_page = entities.story_map_page
transcript_page = entities.transcript_page

_time = events._time
event_detail = events.event_detail
events_page = events.events_page
notice_detail_payload = events.notice_detail_payload
notices_page = events.notices_page

_compact_line = search._compact_line
dialogue_search_page = search.dialogue_search_page
search_page = search.search_page
