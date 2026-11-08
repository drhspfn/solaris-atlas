"""Source attribution and explicit vector/quality invariants."""

import sqlalchemy as sa
from alembic import op

revision = "0031_visual_reference_quality"
down_revision = "0030_visual_memory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout='5s'")
    op.add_column("visual_reference", sa.Column("author", sa.String(512)), schema="core")
    op.create_check_constraint(
        "ck_visual_reference_quality_valid",
        "visual_reference",
        "quality >= 0 AND quality <= 1",
        schema="core",
    )
    op.create_check_constraint(
        "ck_visual_embedding_dimensions_match",
        "visual_embedding",
        "vector_dims(embedding) = dimensions",
        schema="search",
    )


def downgrade() -> None:
    op.drop_constraint("ck_visual_embedding_dimensions_match", "visual_embedding", schema="search")
    op.drop_constraint("ck_visual_reference_quality_valid", "visual_reference", schema="core")
    op.drop_column("visual_reference", "author", schema="core")
