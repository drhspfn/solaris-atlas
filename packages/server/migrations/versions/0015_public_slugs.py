"""Stable public slugs; existing rows are filled by the separate backfill command."""

import sqlalchemy as sa
from alembic import op

revision = "0015_public_slugs"
down_revision = "0014_game_notices"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout = '5s'")
    for table in ("game_event", "game_notice"):
        op.add_column(table, sa.Column(
            "slug", sa.String(512), nullable=True), schema="core")
        op.create_index(f"uq_{table}_slug", table, [
                        "slug"], unique=True, schema="core")
    # No table rewrite or mass UPDATE. The graph index is built without blocking writes.
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE UNIQUE INDEX CONCURRENTLY uq_node_type_slug ON graph.node(type_id, slug)")
    op.execute("""
        CREATE FUNCTION ops.public_slug(identity bigint, title text) RETURNS text
        LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
          SELECT identity::text || '-' || left(coalesce(nullif(trim(both '-' from
            regexp_replace(lower(normalize(regexp_replace(title, '<[^>]*>', '', 'g'), NFKD)),
                           '[^a-z0-9]+', '-', 'g')), ''), 'entry'), 180)
        $$
    """)
    op.execute("""
        CREATE FUNCTION ops.assign_named_node_slug() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.category IN ('character', 'item', 'area', 'quest', 'speaker')
             AND btrim(NEW.title) <> ''
             AND NEW.title NOT ILIKE '%please contact our customer service%'
             AND EXISTS (SELECT 1 FROM i18n.locale WHERE id=NEW.locale_id AND code='en') THEN
            UPDATE graph.node SET slug=ops.public_slug(id, NEW.title)
              WHERE id=NEW.target_node_id AND slug IS NULL AND canonical_key <> NEW.title;
          END IF;
          RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER assign_public_slug AFTER INSERT OR UPDATE OF title ON search.document
        FOR EACH ROW EXECUTE FUNCTION ops.assign_named_node_slug()
    """)
    op.execute("""
        CREATE FUNCTION ops.assign_record_slug() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.slug IS NULL AND btrim(NEW.title) <> '' THEN
            NEW.slug := ops.public_slug(NEW.id, NEW.title);
          END IF;
          RETURN NEW;
        END $$
    """)
    for table in ("game_event", "game_notice"):
        op.execute(f"""CREATE TRIGGER assign_public_slug BEFORE INSERT OR UPDATE OF title ON core.{table}
                       FOR EACH ROW EXECUTE FUNCTION ops.assign_record_slug()""")


def downgrade():
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("DROP TRIGGER assign_public_slug ON search.document")
    op.execute("DROP FUNCTION ops.assign_named_node_slug()")
    for table in ("game_event", "game_notice"):
        op.execute(f"DROP TRIGGER assign_public_slug ON core.{table}")
        op.drop_column(table, "slug", schema="core")
    op.execute("DROP FUNCTION ops.assign_record_slug()")
    op.execute("DROP FUNCTION ops.public_slug(bigint, text)")
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY graph.uq_node_type_slug")
