"""Resolve public families without rewriting source role or speaker identifiers."""

from sqlalchemy import and_, false, func, or_, select

from wuwa_story.db.models.core import Character, DialogueLine
from wuwa_story.db.models.graph import Edge, EdgeEvidence, Node, NodeRevision
from wuwa_story.db.models.ontology import RelationType
from wuwa_story.db.models.raw import SourceFile, SourceRecord
from wuwa_story.ingestion.character_identity import character_kind
from wuwa_story.ingestion.speaker_crosswalk import (
    PERSONAL_PORTRAIT_BASIS,
    PLOT_GUEST_BASIS,
    PLOT_GUEST_PATH,
    SPEAKER_PATH,
)


def speaker_crosswalk(release_id, character_ids=None):
    """The same evidenced identity contract for profiles and relation indexing.

    SpeakerEntityLink is an unversioned convenience projection. Only the exact
    source graph and matching portrait or PlotGuest evidence for this release
    establish membership. Legacy exact_join/card evidence remains archived but
    is not an identity proof.
    """
    proof = select(EdgeEvidence.source_record_id).join(
        SourceRecord, SourceRecord.id == EdgeEvidence.source_record_id,
    ).join(
        SourceFile, SourceFile.id == SourceRecord.source_file_id,
    ).where(
        EdgeEvidence.edge_id == Edge.id, EdgeEvidence.release_id == release_id,
        SourceRecord.release_id == release_id, SourceFile.release_id == release_id,
        or_(*(
            and_(EdgeEvidence.source_file_path == path, SourceFile.logical_source_path == path,
                 EdgeEvidence.explanation.startswith(basis, autoescape=True))
            for path, basis in ((SPEAKER_PATH, PERSONAL_PORTRAIT_BASIS),
                                (PLOT_GUEST_PATH, PLOT_GUEST_BASIS))
        )),
    ).correlate(Edge)
    record = proof.with_only_columns(
        func.min(EdgeEvidence.source_record_id)).scalar_subquery()
    query = select(
        Edge.from_node_id.label(
            'speaker_id'), Edge.to_node_id.label('character_id'),
        Edge.id.label('edge_id'), record.label('source_record_id'),
    ).join(RelationType, RelationType.id == Edge.relation_type_id).where(
        Edge.layer == 'source', Edge.basis == 'exact_join',
        RelationType.key == 'references_character',
        proof.exists(),
    )
    if release_id is None:
        query = query.where(false())
    if character_ids is not None:
        query = query.where(Edge.to_node_id.in_(character_ids))
    return query.subquery()


def dialogue_participation(release_id, *, speaker_ids=None, state_ids=None, line_ids=None):
    """Pinned speaker/action/state membership from source edges, not typed pointers.

    Typed dialogue rows are first-import projections and can point to another
    release. All three graph hops and the returned raw record must be evidenced
    in the requested snapshot, including when a line changes speaker or action.
    """
    spoken, talks, actions = (Edge.__table__.alias(name) for name in (
        'participation_spoken', 'participation_talks', 'participation_actions'))
    proof = EdgeEvidence.__table__.alias('participation_proof')

    def source_hop(edge, relation):
        return and_(
            edge.c.layer == 'source', edge.c.relation_type_id == select(RelationType.id).where(
                RelationType.key == relation).scalar_subquery(),
            select(EdgeEvidence.id).where(
                EdgeEvidence.edge_id == edge.c.id, EdgeEvidence.release_id == release_id,
            ).exists(),
        )

    query = select(
        spoken.c.from_node_id.label(
            'line_id'), spoken.c.to_node_id.label('speaker_id'),
        talks.c.from_node_id.label(
            'action_id'), actions.c.from_node_id.label('state_id'),
        func.min(proof.c.source_record_id).label('source_record_id'),
    ).select_from(spoken).join(
        DialogueLine, DialogueLine.node_id == spoken.c.from_node_id,
    ).join(talks, talks.c.to_node_id == spoken.c.from_node_id).join(
        actions, actions.c.to_node_id == talks.c.from_node_id,
    ).join(proof, and_(proof.c.edge_id == spoken.c.id, proof.c.release_id == release_id)).join(
        SourceRecord, SourceRecord.id == proof.c.source_record_id,
    ).where(
        source_hop(spoken, 'spoken_by'), source_hop(
            talks, 'presents_talk_item'),
        source_hop(
            actions, 'contains_action'), SourceRecord.release_id == release_id,
    )
    if release_id is None:
        query = query.where(false())
    if speaker_ids is not None:
        query = query.where(spoken.c.to_node_id.in_(speaker_ids))
    if state_ids is not None:
        query = query.where(actions.c.from_node_id.in_(state_ids))
    if line_ids is not None:
        query = query.where(spoken.c.from_node_id.in_(line_ids))
    return query.group_by(spoken.c.from_node_id, spoken.c.to_node_id,
                          talks.c.from_node_id, actions.c.from_node_id).subquery()


async def character_family(session, character, node, localize, release_id=None):
    identity = (character.metadata_json or {}).get("public_identity") or {}
    if not identity:
        return [node.id], None
    statement = (
        select(Character, Node).join(Node, Node.id == Character.node_id).where(
            Character.metadata_json["public_identity"]["family"].as_string(
            ) == identity["family"]
        ).order_by(Node.canonical_key)
    )
    if release_id is not None:
        statement = statement.where(select(NodeRevision.id).where(
            NodeRevision.node_id == Node.id, NodeRevision.release_id == release_id).exists())
    rows = list(await session.execute(statement))
    variants = []
    for member, member_node in rows:
        variant = member.metadata_json["public_identity"]
        if not variant.get("selectable", True):
            continue
        variants.append({
            "canonical_key": member_node.canonical_key,
            "gender": variant["gender"], "element_id": variant.get("element_id"),
            "element": await localize(variant.get("element_name_key")),
        })
    return [member.node_id for member, _ in rows], {
        "canonical_key": identity["canonical_key"], "name": "Rover",
        "selected_key": identity.get("variant_key", node.canonical_key), "variants": variants,
    }


def public_character_filter(role_type, identity, canonical_key, kind="playable", *, title=None):
    """Apply category and family paging before LIMIT, in both search backends."""
    category = {
        "playable": role_type == 1,
        "story": role_type.in_((2, 4, 5)),
        "npc": role_type == 6,
        "technical": or_(role_type.is_(None), role_type.not_in((1, 2, 4, 5, 6))),
        "all": True,
    }[kind]
    if title is not None:
        # Unnamed native templates remain addressable graph records, not public
        # NPC cards. Apply this before counting/paging, not after loading rows.
        category = and_(category, or_(role_type.is_(None), role_type != 6,
            and_(func.length(func.trim(title)) > 0, title != canonical_key)))
    primary = identity["canonical_key"].as_string()
    return and_(category, or_(primary.is_(None), primary == canonical_key))


async def annotate_character_cards(session, results):
    ids = [row["id"] for row in results if row.get("node_type") == "character"]
    if not ids:
        return
    metadata = dict((await session.execute(select(Character.node_id, Character.metadata_json).where(Character.node_id.in_(ids)))).all())
    for row in results:
        if row["id"] not in metadata:
            continue
        info = metadata[row["id"]] or {}
        row["character_kind"] = character_kind(info.get("role_type"))
        if info.get("public_identity"):
            row["title"] = "Rover"
