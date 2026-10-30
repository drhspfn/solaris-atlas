"""Select a catalog page before loading its entity and localization details."""

from sqlalchemy import and_, func, literal, or_, select
from sqlalchemy.orm import load_only

from wuwa_story.db.models.core import Character, Item, Location, Quest, Speaker
from wuwa_story.db.models.graph import Node, NodeType
from wuwa_story.db.models.i18n import LocalizationContent, LocalizationValue
from wuwa_story.db.models.search import SearchDocument


async def read_catalog(session, category, locale_id, release_id, limit, offset, quest_type_id):
    node_types = ["area", "location"] if category == "location" else [category]
    entity = {
        "character": Character,
        "item": Item,
        "location": Location,
        "quest": Quest,
        "speaker": Speaker,
    }[category]
    scope = (
        select(Node.id)
        .join(NodeType, NodeType.id == Node.type_id)
        .where(NodeType.key.in_(node_types), Node.status == "active")
    )
    if category == "character":
        role_type = Character.metadata_json["role_type"].as_integer()
        # Preserve primary roles and rows without a role type.
        scope = scope.outerjoin(Character, Character.node_id == Node.id).where(
            or_(role_type == 1, role_type.is_(None))
        )
    elif quest_type_id is not None:
        scope = scope.join(Quest, Quest.node_id == Node.id).where(
            Quest.quest_type == str(quest_type_id)
        )
    count_scope = scope
    if category == "character":
        # The existing count includes only nodes with a character record.
        count_scope = count_scope.where(Character.node_id.is_not(None))
    total = await session.scalar(select(func.count()).select_from(count_scope.subquery())) or 0

    sort_title = func.lower(func.coalesce(SearchDocument.title, Node.canonical_key))
    page = (
        scope.with_only_columns(
            Node.id,
            NodeType.key.label("node_type"),
            SearchDocument.title.label("search_title"),
            SearchDocument.aliases,
            sort_title.label("sort_title"),
        )
        .outerjoin(
            SearchDocument,
            and_(
                SearchDocument.target_node_id == Node.id,
                SearchDocument.locale_id == locale_id,
                SearchDocument.category.in_(node_types),
            ),
        )
        .order_by(sort_title, Node.id)
        .offset(offset)
        .limit(limit)
        .subquery("catalog_page")
    )
    alternate_title = (
        select(SearchDocument.title)
        .where(
            SearchDocument.target_node_id == Node.id,
            SearchDocument.locale_id == locale_id,
            SearchDocument.category == f"{category}_nickname",
        )
        .order_by(SearchDocument.id)
        .limit(1)
        .correlate(Node)
        .scalar_subquery()
    )
    canonical_name = (
        entity.canonical_name if category in {"character", "item", "location"} else literal(None)
    )
    statement = (
        select(
            Node,
            page.c.node_type,
            page.c.search_title,
            page.c.aliases,
            LocalizationContent.content,
            entity.name_key_id,
            canonical_name,
            alternate_title,
        )
        .select_from(page)
        .join(Node, Node.id == page.c.id)
        .outerjoin(entity, entity.node_id == Node.id)
        .outerjoin(
            LocalizationValue,
            and_(
                LocalizationValue.key_id == entity.name_key_id,
                LocalizationValue.locale_id == locale_id,
                LocalizationValue.release_id == release_id,
            ),
        )
        .outerjoin(LocalizationContent, LocalizationContent.id == LocalizationValue.content_id)
        .options(load_only(Node.id, Node.canonical_key, Node.slug))
        .order_by(page.c.sort_title, page.c.id)
    )
    return total, (await session.execute(statement)).all()
