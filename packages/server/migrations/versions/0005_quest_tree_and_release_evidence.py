"""Keep graph evidence per imported snapshot and seed authored quest-tree vocabulary."""

import sqlalchemy as sa
from alembic import op

revision = "0005_quest_tree_evidence"
down_revision = "0004_auth"
branch_labels = None
depends_on = None

NODE_TYPES = ("quest_type", "quest_chapter", "quest_tree_chapter", "quest_tree_node")
RELATION_TYPES = (
    "has_quest_type", "in_quest_chapter", "in_quest_tree_chapter",
    "quest_tree_contains_quest", "quest_tree_predecessor", "quest_tree_next",
    "quest_tree_main_node", "quest_tree_includes_node",
)


def upgrade() -> None:
    for key in NODE_TYPES:
        op.execute(
            "INSERT INTO graph.node_type (id, key, description) "
            "SELECT COALESCE(MAX(id), 0) + 1, '" + key + "', "
            "'Canonical source node type' FROM graph.node_type "
            "ON CONFLICT (key) DO NOTHING"
        )
    for key in RELATION_TYPES:
        op.execute(
            "INSERT INTO ontology.relation_type "
            "(id, key, label, category, directional) "
            "SELECT COALESCE(MAX(id), 0) + 1, '" + key + "', '" +
            key.replace("_", " ").title() + "', 'source', true "
            "FROM ontology.relation_type ON CONFLICT (key) DO NOTHING"
        )
    # 0001 creates tables from current ORM metadata on a fresh database, so a
    # fresh migration run can already have this column and constraint.
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns(
        "edge_evidence", schema="graph"
    )}
    if "release_id" in columns:
        return
    op.add_column("edge_evidence", sa.Column("release_id", sa.BigInteger(), nullable=True), schema="graph")
    op.execute("""
        UPDATE graph.edge_evidence evidence
        SET release_id = COALESCE(
            (SELECT record.release_id FROM raw.source_record record
             WHERE record.id = evidence.source_record_id),
            (SELECT edge.created_release_id FROM graph.edge edge
             WHERE edge.id = evidence.edge_id)
        )
    """)
    op.create_foreign_key(
        "fk_edge_evidence_release_id", "edge_evidence", "game_release",
        ["release_id"], ["id"], source_schema="graph", referent_schema="ops", ondelete="RESTRICT",
    )
    op.create_index("ix_graph_edge_evidence_release_id", "edge_evidence", ["release_id"], schema="graph")
    op.drop_constraint("uq_edge_evidence_source", "edge_evidence", type_="unique", schema="graph")
    op.execute("""
        ALTER TABLE graph.edge_evidence
        ADD CONSTRAINT uq_edge_evidence_source
        UNIQUE NULLS NOT DISTINCT (edge_id, release_id, source_file_path, source_raw_path)
    """)


def downgrade() -> None:
    op.drop_constraint("uq_edge_evidence_source", "edge_evidence", type_="unique", schema="graph")
    op.execute("""
        DELETE FROM graph.edge_evidence duplicate
        USING graph.edge_evidence keeper
        WHERE duplicate.id > keeper.id
          AND duplicate.edge_id = keeper.edge_id
          AND duplicate.source_file_path IS NOT DISTINCT FROM keeper.source_file_path
          AND duplicate.source_raw_path IS NOT DISTINCT FROM keeper.source_raw_path
    """)
    op.execute("""
        ALTER TABLE graph.edge_evidence
        ADD CONSTRAINT uq_edge_evidence_source
        UNIQUE NULLS NOT DISTINCT (edge_id, source_file_path, source_raw_path)
    """)
    op.drop_index("ix_graph_edge_evidence_release_id", table_name="edge_evidence", schema="graph")
    op.drop_constraint("fk_edge_evidence_release_id", "edge_evidence", type_="foreignkey", schema="graph")
    op.drop_column("edge_evidence", "release_id", schema="graph")
    for key in RELATION_TYPES:
        op.execute(f"DELETE FROM ontology.relation_type WHERE key = '{key}'")
    for key in NODE_TYPES:
        op.execute(f"DELETE FROM graph.node_type WHERE key = '{key}'")
