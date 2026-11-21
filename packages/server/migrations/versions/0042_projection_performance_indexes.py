"""Add compact (edge_id, release_id) index on graph.edge_evidence for read projections."""

from alembic import op

revision = "0042_projection_performance_indexes"
down_revision = "0041_app_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout='5s'")
    op.create_index(
        "ix_edge_evidence_edge_release",
        "edge_evidence",
        ["edge_id", "release_id"],
        schema="graph",
        if_not_exists=True,
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout='5s'")
    op.drop_index(
        "ix_edge_evidence_edge_release",
        table_name="edge_evidence",
        schema="graph",
        if_exists=True,
    )
