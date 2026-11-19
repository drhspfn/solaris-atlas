"""Add generic ops.app_setting table for code-declared runtime configuration."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0041_app_settings"
down_revision = "0040_agent_fact_board"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "app_setting",
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("value_type", sa.String(length=24),
                  nullable=False, server_default="string"),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("validation", JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("choices", JSONB(), nullable=True),
        sa.Column("multi", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        sa.Column("is_secret", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        sa.Column("updated_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("key", name="pk_app_setting"),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"], ["auth.user.id"], ondelete="SET NULL"
        ),
        schema="ops",
    )
    op.create_index("ix_app_setting_category", "app_setting",
                    ["category"], schema="ops")


def downgrade():
    op.drop_index("ix_app_setting_category",
                  table_name="app_setting", schema="ops")
    op.drop_table("app_setting", schema="ops")
