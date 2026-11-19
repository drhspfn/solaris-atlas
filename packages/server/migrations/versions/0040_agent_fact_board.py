"""Add shared ops.agent_fact_board table for cross-agent facts, links, and questions."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0040_agent_fact_board"
down_revision = "0039_event_canonical_binding"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "agent_fact_board",
        sa.Column("id", sa.BigInteger(), sa.Identity(
            always=True), primary_key=True),
        sa.Column("key", sa.String(length=256), nullable=False),
        sa.Column("fact_type", sa.String(length=48), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("importance", sa.SmallInteger(),
                  nullable=False, server_default="3"),
        sa.Column("confidence", sa.String(length=24),
                  nullable=False, server_default="observed"),
        sa.Column("source_kind", sa.String(length=48),
                  nullable=False, server_default="quest_analysis"),
        sa.Column("status", sa.String(length=24),
                  nullable=False, server_default="active"),
        sa.Column("subject_node_id", sa.BigInteger(), nullable=True),
        sa.Column("object_node_id", sa.BigInteger(), nullable=True),
        sa.Column("relation_type", sa.String(length=64), nullable=True),
        sa.Column("quest_node_id", sa.BigInteger(), nullable=True),
        sa.Column("cutscene_node_id", sa.BigInteger(), nullable=True),
        sa.Column("question_id", sa.BigInteger(), nullable=True),
        sa.Column("note_id", sa.BigInteger(), nullable=True),
        sa.Column("run_id", sa.BigInteger(), nullable=True),
        sa.Column("tags", JSONB(), nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
        sa.Column("citations", JSONB(), nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
        sa.Column("metadata_json", JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["subject_node_id"], [
                                "graph.node.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["object_node_id"], [
                                "graph.node.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["quest_node_id"], ["graph.node.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["cutscene_node_id"], [
                                "graph.node.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["question_id"], ["ops.agent_work_item.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["note_id"], ["ops.agent_note.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["run_id"], ["ops.processing_run.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("key", name="uq_agent_fact_board_key"),
        schema="ops",
    )
    op.create_index("ix_agent_fact_board_fact_type",
                    "agent_fact_board", ["fact_type"], schema="ops")
    op.create_index("ix_agent_fact_board_status",
                    "agent_fact_board", ["status"], schema="ops")
    op.create_index("ix_agent_fact_board_type_status", "agent_fact_board", [
                    "fact_type", "status"], schema="ops")
    op.create_index("ix_agent_fact_board_importance",
                    "agent_fact_board", ["importance"], schema="ops")
    op.create_index("ix_agent_fact_board_subject_node_id",
                    "agent_fact_board", ["subject_node_id"], schema="ops")
    op.create_index("ix_agent_fact_board_object_node_id",
                    "agent_fact_board", ["object_node_id"], schema="ops")
    op.create_index("ix_agent_fact_board_quest_node_id",
                    "agent_fact_board", ["quest_node_id"], schema="ops")
    op.create_index("ix_agent_fact_board_cutscene_node_id",
                    "agent_fact_board", ["cutscene_node_id"], schema="ops")
    op.create_index("ix_agent_fact_board_question_id",
                    "agent_fact_board", ["question_id"], schema="ops")
    op.create_index("ix_agent_fact_board_note_id",
                    "agent_fact_board", ["note_id"], schema="ops")
    op.create_index("ix_agent_fact_board_run_id",
                    "agent_fact_board", ["run_id"], schema="ops")


def downgrade():
    op.drop_table("agent_fact_board", schema="ops")
