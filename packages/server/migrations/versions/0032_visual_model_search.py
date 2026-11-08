"""Bound exact visual retrieval to the selected model and vector dimensions."""

from alembic import op

revision = "0032_visual_model_search"
down_revision = "0031_visual_reference_quality"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout='5s'")
    op.create_index(
        "ix_visual_embedding_model_dimensions",
        "visual_embedding",
        ["model_id", "dimensions"],
        schema="search",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_visual_embedding_model_dimensions", table_name="visual_embedding", schema="search"
    )
