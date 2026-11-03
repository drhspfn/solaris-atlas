"""Create the frozen initial WuWa Story Platform database foundation."""

from pathlib import Path

from alembic import op
from sqlalchemy.dialects.postgresql import insert

import wuwa_story.db.models  # noqa: F401 - populate Base.metadata
from wuwa_story.db.models.graph import NodeType
from wuwa_story.db.models.i18n import Locale
from wuwa_story.db.models.ontology import EventType, RelationType
from wuwa_story.db.models.status import StatusType
from wuwa_story.db.models.storage import FileType
from wuwa_story.db.seeds import (
    EVENT_TYPES,
    FILE_TYPES,
    LOCALES,
    NODE_TYPES,
    RELATION_TYPES,
    STATUSES,
)

revision = "0001_initial_foundation"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Preserve the originally shipped DDL independently of evolving ORM tables.
    ddl = Path(__file__).resolve().parents[1] / \
        "ddl" / "0001_initial_foundation.sql"
    for statement in ddl.read_text(encoding="utf-8").split("\n-- statement\n")[1:]:
        op.execute(statement.strip())
    bind = op.get_bind()
    bind.execute(
        insert(NodeType)
        .values(
            [
                {"id": index, "key": key,
                    "description": "Canonical source or semantic node type"}
                for index, key in enumerate(NODE_TYPES, start=1)
            ]
        )
        .on_conflict_do_nothing()
    )
    bind.execute(
        insert(RelationType)
        .values(
            [
                {
                    "id": index,
                    "key": key,
                    "label": key.replace("_", " ").title(),
                    "category": "source" if key[:1].islower() else "semantic",
                    "directional": True,
                }
                for index, key in enumerate(RELATION_TYPES, start=1)
            ]
        )
        .on_conflict_do_nothing()
    )
    bind.execute(
        insert(EventType)
        .values(
            [
                {"id": index, "key": key,
                    "label": key.replace("_", " ").title()}
                for index, key in enumerate(EVENT_TYPES, start=1)
            ]
        )
        .on_conflict_do_nothing()
    )
    bind.execute(
        insert(FileType)
        .values(
            [
                {"id": index, "key": key,
                    "description": key.replace("_", " ").title()}
                for index, key in enumerate(FILE_TYPES, start=1)
            ]
        )
        .on_conflict_do_nothing()
    )
    bind.execute(
        insert(Locale)
        .values(
            [
                {"id": index, "code": code, "name": name}
                for index, (code, name) in enumerate(LOCALES.items(), start=1)
            ]
        )
        .on_conflict_do_nothing()
    )
    bind.execute(
        insert(StatusType)
        .values(
            [
                {
                    "category": category,
                    "key": key,
                    "terminal": key
                    in {"succeeded", "failed", "cancelled", "published", "archived"},
                    "description": f"{category} lifecycle state: {key}",
                }
                for category, keys in STATUSES.items()
                for key in keys
            ]
        )
        .on_conflict_do_nothing()
    )


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS search CASCADE")
    op.execute("DROP SCHEMA IF EXISTS content CASCADE")
    op.execute("DROP SCHEMA IF EXISTS story CASCADE")
    op.execute("DROP SCHEMA IF EXISTS core CASCADE")
    op.execute("DROP SCHEMA IF EXISTS graph CASCADE")
    op.execute("DROP SCHEMA IF EXISTS i18n CASCADE")
    op.execute("DROP SCHEMA IF EXISTS raw CASCADE")
    op.execute("DROP SCHEMA IF EXISTS storage CASCADE")
    op.execute("DROP SCHEMA IF EXISTS ontology CASCADE")
    op.execute("DROP SCHEMA IF EXISTS ops CASCADE")
