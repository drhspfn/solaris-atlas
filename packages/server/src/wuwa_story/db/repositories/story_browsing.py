"""SQL reads for quest browsing; request caching and payloads belong to story services."""

from sqlalchemy import ARRAY, Numeric, and_, case, cast, func, or_, select
from sqlalchemy.orm import defer, load_only

from wuwa_story.db.models.core import (
    Character,
    DialogueLine,
    Item,
    Location,
    PlayerChoice,
    Quest,
    QuestAction,
    QuestNode,
    QuestState,
    Speaker,
    SpeakerEntityLink,
    VoiceReference,
)
from wuwa_story.db.models.graph import Edge, EdgeEvidence, Node, NodeRevision, NodeType
from wuwa_story.db.models.i18n import (
    Locale,
    LocalizationContent,
    LocalizationKey,
    LocalizationValue,
)
from wuwa_story.db.models.ontology import RelationType
from wuwa_story.db.models.ops import GameRelease, ImportRun
from wuwa_story.db.models.raw import SourceFile, SourceRecord
from wuwa_story.db.models.search import EntityAlias, SearchDocument
from wuwa_story.db.models.story import Scene
from wuwa_story.db.repositories.quest_scope import quest_state_links, quest_states_query


async def fetch_releases(session):
    return list(await session.scalars(select(GameRelease)))


async def completed_release_ids(session):
    return set(await session.scalars(select(ImportRun.release_id).where(ImportRun.status == "succeeded")))


async def locale_id(session, code):
    return await session.scalar(select(Locale.id).where(Locale.code == code))


async def locale(session, code):
    return await session.scalar(select(Locale).where(Locale.code == code))


async def node(session, identity):
    return await session.get(Node, identity)


async def quest_by_game_id(session, identity):
    return await session.scalar(select(Quest).where(Quest.game_quest_id == identity))


async def localization_keys(session, identities):
    return dict(
        (
            await session.execute(
                select(LocalizationKey.id, LocalizationKey.key).where(
                    LocalizationKey.id.in_(identities)
                )
            )
        ).all()
    )


async def named_localization_keys(session, keys):
    return dict((await session.execute(select(LocalizationKey.key, LocalizationKey.id)
                                     .where(LocalizationKey.key.in_(keys)))).all()) if keys else {}


async def illustration_actions(session, state_ids):
    return list(await session.execute(select(QuestAction.node_id, QuestAction.quest_state_node_id,
        QuestAction.params['TalkItems']).where(QuestAction.quest_state_node_id.in_(state_ids),
        QuestAction.action_name == 'ShowTalk').order_by(QuestAction.quest_state_node_id, QuestAction.action_index)))


async def localization_values(session, identities, locale_id):
    return await session.execute(
        select(
            LocalizationValue.key_id,
            LocalizationValue.release_id,
            LocalizationValue.status,
            LocalizationContent.content.label("content"),
            LocalizationValue.source_record_id,
        )
        .outerjoin(LocalizationContent, LocalizationContent.id == LocalizationValue.content_id)
        .where(LocalizationValue.key_id.in_(identities), LocalizationValue.locale_id == locale_id)
    )


async def node_label_rows(session, batch, locale_id):
    rows = list(
        await session.execute(
            select(
                Node,
                NodeType.key,
                func.coalesce(
                    Character.name_key_id,
                    Item.name_key_id,
                    Location.name_key_id,
                    Quest.name_key_id,
                    Speaker.name_key_id,
                ),
                func.coalesce(
                    Character.canonical_name, Item.canonical_name, Location.canonical_name
                ),
            )
            .join(NodeType, NodeType.id == Node.type_id)
            .outerjoin(Character, Character.node_id == Node.id)
            .outerjoin(Item, Item.node_id == Node.id)
            .outerjoin(Location, Location.node_id == Node.id)
            .outerjoin(Quest, Quest.node_id == Node.id)
            .outerjoin(Speaker, Speaker.node_id == Node.id)
            .where(Node.id.in_(batch))
        )
    )
    aliases = {}
    if locale_id is not None:
        for identity, alias in await session.execute(
            select(EntityAlias.node_id, EntityAlias.alias)
            .where(EntityAlias.node_id.in_(batch), EntityAlias.locale_id == locale_id)
            .order_by(EntityAlias.id)
        ):
            aliases.setdefault(identity, alias)
        missing = set(batch) - aliases.keys()
        if missing:
            for identity, title in await session.execute(
                select(SearchDocument.target_node_id, SearchDocument.title)
                .where(
                    SearchDocument.target_node_id.in_(missing),
                    SearchDocument.locale_id == locale_id,
                )
                .order_by(SearchDocument.id)
            ):
                aliases.setdefault(identity, title)
    return rows, aliases


async def dialogue_choices(session, batch):
    return list(
        await session.execute(
            select(Edge.from_node_id, PlayerChoice, Node.canonical_key, SourceRecord, SourceFile)
            .join(RelationType, RelationType.id == Edge.relation_type_id)
            .join(PlayerChoice, PlayerChoice.node_id == Edge.to_node_id)
            .join(Node, Node.id == PlayerChoice.node_id)
            .outerjoin(SourceRecord, SourceRecord.id == PlayerChoice.source_record_id)
            .outerjoin(SourceFile, SourceFile.id == SourceRecord.source_file_id)
            .where(Edge.from_node_id.in_(batch), RelationType.key == "presents_choice")
        )
    )


async def choice_targets(session, choice_ids):
    return await session.execute(
        select(Edge.from_node_id, Node.canonical_key)
        .join(RelationType, RelationType.id == Edge.relation_type_id)
        .join(Node, Node.id == Edge.to_node_id)
        .where(Edge.from_node_id.in_(choice_ids), RelationType.key == "choice_branch")
    )


async def nested_actions(session, unresolved):
    return await session.execute(
        select(Edge.from_node_id, Edge.to_node_id)
        .join(RelationType, RelationType.id == Edge.relation_type_id)
        .where(Edge.from_node_id.in_(unresolved), RelationType.key == "contains_nested_action")
    )


async def nested_targets(session, owners):
    return await session.execute(
        select(Edge.from_node_id, Node.canonical_key)
        .join(RelationType, RelationType.id == Edge.relation_type_id)
        .join(Node, Node.id == Edge.to_node_id)
        .where(Edge.from_node_id.in_(owners), RelationType.key == "jumps_to_talk")
    )


async def load_action_params(session, identities):
    return list(
        await session.scalars(
            select(QuestAction)
            .where(QuestAction.node_id.in_(identities))
            .options(load_only(QuestAction.node_id, QuestAction.params))
        )
    )


async def load_raw_records(session, identities):
    return list(
        await session.scalars(
            select(SourceRecord)
            .where(SourceRecord.id.in_(identities))
            .options(load_only(SourceRecord.id, SourceRecord.data))
        )
    )


async def entity_links(session, node_id, limit=200):
    neighbor_id = case((Edge.from_node_id == node_id, Edge.to_node_id), else_=Edge.from_node_id)
    rows = await session.execute(
        select(Edge, RelationType.key, Node, NodeType.key)
        .join(RelationType, RelationType.id == Edge.relation_type_id)
        .join(Node, Node.id == neighbor_id)
        .join(NodeType, NodeType.id == Node.type_id)
        .where(
            or_(Edge.from_node_id == node_id, Edge.to_node_id == node_id),
            neighbor_id != node_id,
            Edge.layer.in_(["source", "canonical"]),
            NodeType.key != "source_reference",
        )
        .order_by(RelationType.key, Node.canonical_key, Edge.id)
        .limit(limit)
    )
    rows = list(rows)
    return rows


async def quest_state_ids(session, quest_node_id, release_id=None):
    links = quest_state_links(release_id, quest_node_id=quest_node_id)
    return list(
        await session.scalars(select(links.c.state_id).where(links.c.quest_id == quest_node_id))
    )


async def quest_page_state_ids(session, quest_node_id, release_id=None):
    return list(await session.scalars(quest_states_query(quest_node_id, release_id)))


async def quest_area_rows(session, quest_node_id):
    quest_nodes = (
        select(Edge.to_node_id)
        .join(RelationType, RelationType.id == Edge.relation_type_id)
        .where(Edge.from_node_id == quest_node_id, RelationType.key == "has_quest_node")
    )
    area_rows = await session.execute(
        select(Edge, Node)
        .join(RelationType, RelationType.id == Edge.relation_type_id)
        .join(Node, Node.id == Edge.to_node_id)
        .where(
            Edge.from_node_id.in_(quest_nodes),
            RelationType.key == "references_area",
            Edge.layer == "source",
        )
        .order_by(Node.canonical_key, Edge.id)
    )
    return area_rows


async def latest_source_row(session, node_id):
    # Match version_key's numeric component ordering, then break ties by import.
    components = func.btrim(
        func.regexp_replace(func.coalesce(GameRelease.game_version, ""), "[^0-9]+", ".", "g"),
        ".",
    )
    version = cast(func.string_to_array(components, "."), ARRAY(Numeric))
    result = await session.execute(
        select(NodeRevision, SourceRecord, SourceFile, GameRelease)
        .join(SourceRecord, SourceRecord.id == NodeRevision.source_record_id)
        .join(SourceFile, SourceFile.id == SourceRecord.source_file_id)
        .outerjoin(GameRelease, GameRelease.id == NodeRevision.release_id)
        .where(NodeRevision.node_id == node_id)
        .order_by(version.desc(), GameRelease.sequence.desc().nulls_last(),
                  NodeRevision.revision.desc(), NodeRevision.id.desc())
        .limit(1)
    )
    row = result.first()
    return row


async def quest_node_rows(session, game_quest_id):
    quest_nodes = await session.execute(
        select(QuestNode, Node, SourceRecord, SourceFile)
        .join(Node, Node.id == QuestNode.node_id)
        .outerjoin(SourceRecord, SourceRecord.id == QuestNode.source_record_id)
        .outerjoin(SourceFile, SourceFile.id == SourceRecord.source_file_id)
        .where(QuestNode.game_quest_id == game_quest_id)
        .order_by(SourceFile.logical_source_path, SourceRecord.row_index, QuestNode.game_node_id)
        .limit(2000)
    )
    qnode_rows = list(quest_nodes)
    return qnode_rows


async def quest_hierarchy_rows(session, qnode_ids):
    hierarchy_rows = []
    if qnode_ids:
        hierarchy_rows = list(
            await session.execute(
                select(Edge, RelationType.key, Node, NodeType.key)
                .join(RelationType, RelationType.id == Edge.relation_type_id)
                .join(Node, Node.id == Edge.to_node_id)
                .join(NodeType, NodeType.id == Node.type_id)
                .where(
                    Edge.from_node_id.in_(qnode_ids),
                    Edge.to_node_id.in_(qnode_ids),
                    RelationType.key.in_(["parent_of", "requires_quest", "checks_quest_node"]),
                    Edge.layer == "source",
                )
                .order_by(RelationType.key, Edge.from_node_id, Edge.to_node_id)
            )
        )
    return hierarchy_rows


async def quest_states(session, state_ids):
    states = list(
        await session.scalars(
            select(QuestState)
            .where(QuestState.node_id.in_(state_ids))
            .order_by(QuestState.flow_id, QuestState.state_id, QuestState.state_key)
        )
    )
    return states


async def quest_actions(session, state_ids):
    return await session.execute(
        select(QuestAction, Node.canonical_key)
        .join(Node, Node.id == QuestAction.node_id)
        .where(QuestAction.quest_state_node_id.in_(state_ids))
        .order_by(QuestAction.action_index, QuestAction.node_id)
    )


async def quest_scenes(session, quest_node_id):
    plot_steps = (
        select(Edge.to_node_id)
        .join(RelationType, RelationType.id == Edge.relation_type_id)
        .where(Edge.from_node_id == quest_node_id, RelationType.key == "has_plot_step")
    )
    scene_ids = (
        select(Edge.to_node_id)
        .join(RelationType, RelationType.id == Edge.relation_type_id)
        .where(Edge.from_node_id.in_(plot_steps), RelationType.key == "presents_scene")
    )
    scenes = await session.execute(
        select(Scene, Node.canonical_key)
        .outerjoin(Node, Node.id == Scene.node_id)
        .where(Scene.node_id.in_(scene_ids))
        .order_by(Scene.authored_order, Scene.node_id)
    )
    return list(scenes)


async def quest_requirement_records(session, release_id, keys, source_path):
    requirement_rows = await session.execute(
        select(SourceRecord)
        .join(SourceFile, SourceFile.id == SourceRecord.source_file_id)
        .where(
            SourceRecord.release_id == release_id,
            SourceFile.logical_source_path == source_path,
            SourceRecord.data["Key"].astext.in_(keys),
        )
    )
    return list(requirement_rows.scalars())


async def quest_requirement_items(session, keys):
    items = {}
    for offset in range(0, len(keys), 500):
        batch = keys[offset : offset + 500]
        rows = await session.scalars(
            select(Node).join(NodeType).where(
                NodeType.key == "item", or_(Node.canonical_key.in_(batch), Node.slug.in_(batch))
            )
        )
        for node in rows:
            if node.slug in batch:
                items.setdefault(node.slug, node)
            if node.canonical_key in batch:
                items[node.canonical_key] = node
    return items


async def search_dialogue_rows(
    session, q, locale_row, release_id, character_node_id, states, limit, offset
):
    statement = (
        select(DialogueLine, QuestAction, QuestState, Node, SourceRecord, SourceFile)
        .options(
            defer(QuestAction.params),
            defer(QuestAction.metadata_json),
            defer(QuestState.metadata_json),
            defer(Node.metadata_json),
            defer(SourceRecord.data),
            defer(SourceFile.metadata_json),
            defer(DialogueLine.metadata_json),
        )
        .join(Node, Node.id == DialogueLine.node_id)
        .outerjoin(QuestAction, QuestAction.node_id == DialogueLine.action_node_id)
        .outerjoin(QuestState, QuestState.node_id == QuestAction.quest_state_node_id)
        .outerjoin(SourceRecord, SourceRecord.id == DialogueLine.source_record_id)
        .outerjoin(SourceFile, SourceFile.id == SourceRecord.source_file_id)
        .outerjoin(LocalizationKey, LocalizationKey.id == DialogueLine.localization_key_id)
        .outerjoin(
            LocalizationValue,
            and_(
                LocalizationValue.key_id == DialogueLine.localization_key_id,
                LocalizationValue.locale_id == locale_row.id,
                LocalizationValue.release_id == release_id,
            ),
        )
        .outerjoin(LocalizationContent, LocalizationContent.id == LocalizationValue.content_id)
        .where(
            or_(
                LocalizationContent.content.ilike(f"%{q}%"),
                DialogueLine.inline_text.ilike(f"%{q}%"),
            )
        )
    )
    if character_node_id is not None:
        speaker_ids = select(SpeakerEntityLink.speaker_node_id).where(
            SpeakerEntityLink.entity_node_id == character_node_id
        )
        statement = statement.where(DialogueLine.speaker_node_id.in_(speaker_ids))
    if states is not None:
        statement = statement.where(QuestAction.quest_state_node_id.in_(states))
    statement = (
        statement.order_by(
            QuestState.state_key,
            QuestAction.action_index,
            DialogueLine.source_index,
            DialogueLine.node_id,
        )
        .offset(offset)
        .limit(limit)
    )
    rows = list((await session.execute(statement)).all())
    return rows


async def quest_transcript_rows(
    session, states, release_id, character_node_id, q, locale_row, focus_line, limit, offset,
    ordered_states=None,
):
    statement = (
        select(DialogueLine, QuestAction, QuestState, Node, SourceRecord, SourceFile)
        .options(
            defer(QuestAction.params),
            defer(QuestAction.metadata_json),
            defer(QuestState.metadata_json),
            defer(Node.metadata_json),
            defer(SourceRecord.data),
            defer(SourceFile.metadata_json),
            defer(DialogueLine.metadata_json),
        )
        .join(QuestAction, QuestAction.node_id == DialogueLine.action_node_id)
        .join(QuestState, QuestState.node_id == QuestAction.quest_state_node_id)
        .join(Node, Node.id == DialogueLine.node_id)
        .outerjoin(SourceRecord, SourceRecord.id == DialogueLine.source_record_id)
        .outerjoin(SourceFile, SourceFile.id == SourceRecord.source_file_id)
        .where(QuestAction.quest_state_node_id.in_(states))
    )
    if character_node_id is not None:
        speaker_ids = select(SpeakerEntityLink.speaker_node_id).where(
            SpeakerEntityLink.entity_node_id == character_node_id
        )
        statement = statement.where(DialogueLine.speaker_node_id.in_(speaker_ids))
    if q:
        value = (
            select(LocalizationValue.key_id)
            .join(LocalizationKey, LocalizationKey.id == LocalizationValue.key_id)
            .outerjoin(LocalizationContent, LocalizationContent.id == LocalizationValue.content_id)
            .where(
                LocalizationValue.release_id == release_id,
                LocalizationValue.locale_id == locale_row.id if locale_row else False,
                LocalizationContent.content.ilike(f"%{q}%"),
            )
        )
        statement = statement.where(
            or_(
                DialogueLine.localization_key_id.in_(value),
                DialogueLine.inline_text.ilike(f"%{q}%"),
            )
        )
    ordering = (
        QuestState.state_key,
        QuestAction.action_index,
        DialogueLine.source_index,
        DialogueLine.node_id,
    )
    if ordered_states:
        rank = {identity: index for index, identity in enumerate(ordered_states)}
        ordering = (case(rank, value=QuestState.node_id, else_=len(rank)), *ordering)
    if focus_line:
        ranked = statement.with_only_columns(
            Node.canonical_key.label("key"),
            func.row_number().over(order_by=ordering).label("position"),
        ).subquery()
        position = await session.scalar(select(ranked.c.position).where(ranked.c.key == focus_line))
        if position is None:
            return None, offset
        offset = max(0, int(position) - 1 - limit // 2)
    statement = statement.order_by(*ordering).offset(offset).limit(limit)
    rows = list((await session.execute(statement)).all())
    return rows, offset


async def media_link_rows(session, from_ids, relations, release_id):
    rows = await session.execute(
        select(Edge.from_node_id, Edge.to_node_id, Node.canonical_key,
               RelationType.key, Edge.basis, EdgeEvidence.source_file_path,
               EdgeEvidence.source_raw_path)
        .join(RelationType, RelationType.id == Edge.relation_type_id)
        .join(Node, Node.id == Edge.to_node_id)
        .join(EdgeEvidence, EdgeEvidence.edge_id == Edge.id)
        .where(Edge.from_node_id.in_(from_ids), RelationType.key.in_(relations),
               Edge.layer == "source", EdgeEvidence.release_id == release_id)
        .order_by(Edge.from_node_id, Edge.id, EdgeEvidence.id)
    )
    return rows


async def voice_references(session, voice_ids):
    return await session.scalars(select(VoiceReference).where(VoiceReference.node_id.in_(voice_ids)))
