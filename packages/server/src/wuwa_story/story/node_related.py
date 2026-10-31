"""Source-backed neighbor browsing shared by HTTP pages and full background builds."""

from sqlalchemy import case, func, select

from wuwa_story.api.source_labels import source_family_label
from wuwa_story.db.models.graph import Edge, Node, NodeType
from wuwa_story.db.models.i18n import Locale
from wuwa_story.db.models.ontology import RelationType
from wuwa_story.db.models.search import SearchDocument
from wuwa_story.read_models.context import SOURCE_BUILD
from wuwa_story.urls import node_address


def _incident_edges(node_id: int, direction: str):
    """Resolve indexed endpoints before joining the potentially large node table."""

    def branch(endpoint, neighbor):
        return select(
            Edge.id.label("edge_id"),
            Edge.relation_type_id,
            neighbor.label("neighbor_id"),
        ).where(endpoint == node_id, Edge.layer.in_(["source", "canonical"]))

    outgoing = branch(Edge.from_node_id, Edge.to_node_id)
    incoming = branch(Edge.to_node_id, Edge.from_node_id)
    if direction == "out":
        statement = outgoing
    elif direction == "in":
        statement = incoming
    else:
        # A self-loop belongs to both directions but occurs once in "both".
        statement = outgoing.union_all(incoming.where(Edge.from_node_id != node_id))
    return statement.cte("incident_edges").prefix_with("MATERIALIZED", dialect="postgresql")


async def source_related(
    session,
    node_id,
    canonical_key,
    *,
    direction="both",
    relation=None,
    category=None,
    source_file=None,
    include_evidence=False,
    locale="en",
    limit=100,
    offset=0,
):
    """Read ordered neighbors; limit=None is internal, complete, offset-zero mode."""
    if direction not in {"both", "in", "out"}:
        raise ValueError("Invalid relation direction")
    if limit is None and offset:
        raise ValueError("Unbounded related reads start at offset zero")
    incident = _incident_edges(node_id, direction)
    statement = (
        select(Edge, RelationType.key, Node, NodeType.key)
        .select_from(incident)
        .join(Edge, Edge.id == incident.c.edge_id)
        .join(RelationType, RelationType.id == incident.c.relation_type_id)
        .join(Node, Node.id == incident.c.neighbor_id)
        .join(NodeType, NodeType.id == Node.type_id)
    )
    if relation:
        statement = statement.where(RelationType.key == relation)
    if category:
        statement = statement.where(NodeType.key == category)
    evidence_requested = (
        include_evidence or category == "source_reference" or source_file is not None
    )
    if not evidence_requested:
        statement = statement.where(NodeType.key != "source_reference")
    if source_file:
        statement = statement.where(Node.metadata_json["source_file"].as_string() == source_file)
    locale_id = await session.scalar(select(Locale.id).where(Locale.code == locale))
    label = (
        select(SearchDocument.title)
        .where(
            SearchDocument.target_node_id == Node.id,
            SearchDocument.locale_id == locale_id,
            SearchDocument.category.in_(
                ["character", "character_nickname", "item", "area", "quest", "speaker"]
            ),
        )
        .order_by(
            case(
                (SearchDocument.category == NodeType.key, 0),
                else_=1,
            ),
            SearchDocument.id,
        )
        .limit(1)
        .scalar_subquery()
    )
    statement = statement.add_columns(label.label("localized_label"))
    rows = await session.execute(
        statement.order_by(RelationType.key, Node.canonical_key, Edge.id)
        .offset(offset)
        .limit(limit)
    )
    # Evidence is independent of page, category, source-file filter and locale.
    # Only source builders own a bounded request-cache lifetime on this session.
    evidence_cache = (
        session.info.setdefault("node_related_evidence", {}) if SOURCE_BUILD.get() else None
    )
    evidence_key = (node_id, direction, relation)
    evidence_summary = evidence_cache.get(evidence_key) if evidence_cache is not None else None
    if evidence_summary is None:
        source_file_value = Node.metadata_json["source_file"].as_string()
        evidence_query = (
            select(source_file_value, RelationType.key, func.count())
            .select_from(incident)
            .join(RelationType, RelationType.id == incident.c.relation_type_id)
            .join(Node, Node.id == incident.c.neighbor_id)
            .join(NodeType, NodeType.id == Node.type_id)
            .where(NodeType.key == "source_reference")
            .group_by(source_file_value, RelationType.key)
            .order_by(func.count().desc(), source_file_value.asc().nullslast(), RelationType.key)
        )
        if relation:
            evidence_query = evidence_query.where(RelationType.key == relation)
        evidence_rows = await session.execute(evidence_query)
        evidence_summary = [
            {
                "label": source_family_label(file),
                "source_file": file,
                "relation": relation_key,
                "record_count": count,
            }
            for file, relation_key, count in evidence_rows
        ]
        if evidence_cache is not None:
            evidence_cache[evidence_key] = evidence_summary
    results = []
    for edge, relation_key, neighbor, node_type, localized_label in rows:
        fallback = None
        if fallback is None and node_type == "source_reference":
            neighbor_source_file = neighbor.metadata_json.get("source_file")
            source_row = neighbor.metadata_json.get("source_row")
            if neighbor_source_file:
                fallback = f"{neighbor_source_file}" + (
                    f" row {source_row}" if source_row is not None else ""
                )
        results.append(
            {
                "relation": relation_key,
                "direction": "outgoing" if edge.from_node_id == node_id else "incoming",
                "node": {
                    "id": neighbor.id,
                    "canonical_key": neighbor.canonical_key,
                    **node_address(neighbor, node_type),
                    "type": node_type,
                    "label": localized_label or fallback or neighbor.canonical_key,
                },
                "basis": edge.basis,
                "provenance": edge.metadata_json,
                **(
                    {"_source_file": neighbor.metadata_json.get("source_file")}
                    if limit is None
                    else {}
                ),
            }
        )
    return {
        "node": canonical_key,
        "summary": (
            f"Found {len(results)} linked entities."
            if results
            else "No linked entity neighbors were found. Source records below are raw game configuration references, not inferred character or story relationships."
        ),
        "direction": direction,
        "relation_filter": relation,
        "category_filter": category,
        "source_file_filter": source_file,
        "include_evidence": evidence_requested,
        "source_evidence": evidence_summary,
        "locale": locale,
        "limit": limit,
        "offset": offset,
        "results": results,
    }
