"""Durable browse projections and transactional, owner-scoped refresh work."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0024_browse_read_models"
down_revision = "0023_search_body_trgm"
branch_labels = None
depends_on = None

KINDS = (
    "character", "item", "location", "quest", "game_event", "game_notice",
    "speaker", "dialogue", "cutscene", "story_map",
)
HISTORICAL_KINDS = tuple(kind for kind in KINDS if kind not in {"location", "speaker", "game_notice"})
DIRECT_TABLES = {
    "core.character": ("character", "node_id"),
    "core.item": ("item", "node_id"),
    "core.location": ("location", "node_id"),
    "core.quest": ("quest", "node_id"),
    "core.speaker": ("speaker", "node_id"),
    "core.dialogue_line": ("dialogue", "node_id"),
    "core.cutscene": ("cutscene", "node_id"),
    "core.game_event": ("game_event", "id"),
    "core.game_event_occurrence": ("game_event", "event_id"),
    "core.game_notice": ("game_notice", "id"),
    "core.game_notice_revision": ("game_notice", "notice_id"),
}
STORAGE_TABLES = {
    "storage.file_reference": "reference",
    "storage.file_location": "file_id",
    "storage.file_object": "id",
}
MEDIA_TABLES = ("core.media_asset", "core.voice_reference", "core.audio_event")
NODE_TABLES = {
    "graph.node": ("id",), "graph.node_revision": ("node_id",),
    "graph.edge": ("from_node_id", "to_node_id"),
    "core.media_asset": ("node_id",), "core.voice_reference": ("node_id", "media_asset_node_id"),
    "core.audio_event": ("node_id",), "search.entity_alias": ("node_id",),
    "search.document": ("target_node_id",),
    "core.speaker_entity_link": ("speaker_node_id", "entity_node_id"),
    "content.document": ("node_id",), "content.document_head": ("node_id",),
    "content.document_reference": ("document_id",),
    "graph.edge_evidence": ("edge_id",),
    "story.scene": ("node_id", "quest_node_id"),
}
# These analysis facts are not read by projection builders. Their published
# document and graph-edge dependencies have exact-owner triggers above.
ANALYSIS_ONLY_TABLES = (
    "story.claim", "story.claim_evidence", "story.event", "search.explanation_embedding",
)
READ_TABLES = tuple(f"core.{kind}_read" for kind in KINDS) + ("core.entity_read_relation",)


def _create_tables():
    # 0001 creates current ORM metadata on fresh installs; upgraded databases
    # need the same explicit DDL here. Never replace an already-created table.
    existing = set(sa.inspect(op.get_bind()).get_table_names(schema="core"))
    for kind in KINDS:
        name = f"{kind}_read"
        if name not in existing:
            op.create_table(
                name,
                sa.Column("entity_id", sa.BigInteger(), primary_key=True, autoincrement=False),
                sa.Column("locale_id", sa.SmallInteger(), primary_key=True),
                sa.Column("snapshot_key", sa.String(64), primary_key=True, server_default="current"),
                sa.Column("title", sa.Text(), nullable=False),
                sa.Column("sort_title", sa.Text(), nullable=False),
                sa.Column("searchable_text", sa.Text(), nullable=False, server_default=""),
                sa.Column("search_vector", postgresql.TSVECTOR(), sa.Computed(
                    "to_tsvector('simple'::regconfig, coalesce(title, '') || ' ' || coalesce(searchable_text, ''))",
                    persisted=True,
                ), nullable=False),
                sa.Column("card", postgresql.JSONB(none_as_null=True), nullable=False),
                sa.Column("role_type", sa.Integer()),
                sa.Column("quest_type", sa.String(64)),
                sa.Column("canonical_key", sa.String(512)),
                sa.Column("slug", sa.String(512)),
                sa.Column("primary_image_file_id", sa.BigInteger()),
                sa.Column("primary_image_url", sa.Text()),
                sa.Column("artwork", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
                sa.Column("payload", postgresql.JSONB(none_as_null=True), nullable=False),
                sa.Column("generation", sa.BigInteger(), nullable=False),
                sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
                schema="core",
            )
        op.execute(f"CREATE INDEX IF NOT EXISTS ix_{kind}_read_catalog ON core.{name} (locale_id, snapshot_key, sort_title, entity_id)")
        op.execute(f"CREATE INDEX IF NOT EXISTS ix_{kind}_read_canonical ON core.{name} (locale_id, snapshot_key, canonical_key) WHERE canonical_key IS NOT NULL")
        op.execute(f"CREATE INDEX IF NOT EXISTS ix_{kind}_read_slug ON core.{name} (locale_id, snapshot_key, slug) WHERE slug IS NOT NULL")
        if kind != "story_map":
            op.execute(f"CREATE INDEX IF NOT EXISTS ix_{kind}_read_search_vector ON core.{name} USING gin (search_vector)")
            op.execute(f"CREATE INDEX IF NOT EXISTS ix_{kind}_read_title_trgm ON core.{name} USING gin (title gin_trgm_ops)")
            op.execute(f"CREATE INDEX IF NOT EXISTS ix_{kind}_read_searchable_text_trgm ON core.{name} USING gin (searchable_text gin_trgm_ops)")
        if kind == "character":
            op.execute("CREATE INDEX IF NOT EXISTS ix_character_read_role_catalog ON core.character_read (locale_id, snapshot_key, role_type, sort_title, entity_id)")
        if kind == "quest":
            op.execute("CREATE INDEX IF NOT EXISTS ix_quest_read_type_catalog ON core.quest_read (locale_id, snapshot_key, quest_type, sort_title, entity_id)")
    if "entity_read_relation" not in existing:
        op.create_table(
            "entity_read_relation",
            sa.Column("owner_kind", sa.String(32), primary_key=True),
            sa.Column("owner_id", sa.BigInteger(), primary_key=True, autoincrement=False),
            sa.Column("related_kind", sa.String(32), primary_key=True),
            sa.Column("related_id", sa.BigInteger(), primary_key=True, autoincrement=False),
            sa.Column("relation_type", sa.String(128), primary_key=True),
            sa.Column("snapshot_key", sa.String(64), primary_key=True, server_default="current"),
            sa.Column("evidence", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
            schema="core",
        )
    if not sa.inspect(op.get_bind()).has_table("read_projection_dirty", schema="ops"):
        op.create_table(
            "read_projection_dirty",
            sa.Column("kind", sa.String(32), primary_key=True),
            sa.Column("entity_id", sa.BigInteger(), primary_key=True, server_default="0", autoincrement=False),
            sa.Column("snapshot_key", sa.String(256), primary_key=True, server_default="current"),
            sa.Column("requested_generation", sa.BigInteger(), nullable=False, server_default="1"),
            sa.Column("processed_generation", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_error", sa.Text()),
            sa.Column("lease_until", sa.DateTime(timezone=True)),
            sa.Column("lease_token", postgresql.UUID(as_uuid=True)),
            sa.CheckConstraint("entity_id >= 0", name="nonnegative_entity_id"),
            sa.CheckConstraint("requested_generation >= 1 AND processed_generation >= 0 AND processed_generation <= requested_generation", name="valid_generations"),
            sa.CheckConstraint("attempts >= 0", name="nonnegative_attempts"),
            sa.CheckConstraint("(lease_until IS NULL) = (lease_token IS NULL)", name="paired_lease"),
            schema="ops",
        )
    op.execute("CREATE INDEX IF NOT EXISTS ix_read_projection_dirty_pending ON ops.read_projection_dirty (available_at, lease_until) WHERE requested_generation > processed_generation")
    op.execute("CREATE INDEX IF NOT EXISTS ix_read_projection_dirty_scope_pending ON ops.read_projection_dirty (kind, snapshot_key) WHERE requested_generation > processed_generation")
    if not sa.inspect(op.get_bind()).has_table("read_projection_scope", schema="ops"):
        op.create_table(
            "read_projection_scope",
            sa.Column("kind", sa.String(32), primary_key=True),
            sa.Column("snapshot_key", sa.String(256), primary_key=True),
            sa.Column("generation", sa.BigInteger(), nullable=False, server_default="1"),
            sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
            sa.Column("published_generation", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("published_at", sa.DateTime(timezone=True)),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.CheckConstraint("generation >= 1", name="positive_generation"),
            sa.CheckConstraint("status IN ('pending', 'building', 'ready')", name="valid_status"),
            sa.CheckConstraint("published_generation >= 0 AND published_generation <= generation", name="valid_published_generation"),
            sa.CheckConstraint("(published_generation = 0) = (published_at IS NULL)", name="paired_publication"),
            schema="ops",
        )


def upgrade():
    op.execute("SET LOCAL lock_timeout = '10s'")
    _create_tables()
    op.execute("""
        CREATE FUNCTION ops.mark_read_projection(p_kind text, p_entity bigint DEFAULT 0,
                                                p_snapshot text DEFAULT 'current') RETURNS void
        LANGUAGE plpgsql AS $$
        DECLARE snapshot text := p_snapshot; history text; created boolean;
        BEGIN
            IF p_snapshot LIKE 'release:%' THEN
                SELECT game_version INTO snapshot FROM ops.game_release
                WHERE id = substring(p_snapshot FROM 9)::bigint;
                IF snapshot IS NULL THEN RETURN; END IF;
            END IF;
            IF p_kind IN ('location','speaker','game_notice') THEN snapshot := 'current'; END IF;
            -- Historical storage is opt-in. Source writes may refresh a selected
            -- snapshot but must never activate every imported release implicitly.
            IF snapshot <> 'current' AND snapshot NOT LIKE 'download:%' AND NOT EXISTS (
                SELECT 1 FROM ops.read_projection_scope WHERE kind=p_kind AND snapshot_key=snapshot
            ) THEN RETURN; END IF;
            -- Scope locks precede dirty-row locks in both writers and publishers.
            INSERT INTO ops.read_projection_scope AS coverage (kind, snapshot_key)
            VALUES (p_kind, snapshot)
            ON CONFLICT (kind, snapshot_key) DO NOTHING RETURNING true INTO created;
            IF created IS NOT TRUE THEN
                UPDATE ops.read_projection_scope SET generation=generation+1,
                    status='pending', updated_at=clock_timestamp()
                WHERE kind=p_kind AND snapshot_key=snapshot;
            ELSIF p_entity > 0 THEN
                -- A newly discovered snapshot needs complete coverage, not just
                -- the one entity that first introduced it.
                INSERT INTO ops.read_projection_dirty(kind,entity_id,snapshot_key,available_at)
                VALUES(p_kind,0,snapshot,clock_timestamp()+interval '2 seconds')
                ON CONFLICT DO NOTHING;
            END IF;
            INSERT INTO ops.read_projection_dirty AS dirty (kind, entity_id, snapshot_key, available_at)
            VALUES (p_kind, p_entity, snapshot, clock_timestamp() + interval '2 seconds')
            ON CONFLICT (kind, entity_id, snapshot_key) DO UPDATE
            SET requested_generation = dirty.requested_generation + 1,
                available_at = clock_timestamp() + interval '2 seconds';
            IF p_entity > 0 AND snapshot = 'current' AND p_kind NOT IN ('location','speaker','game_notice') THEN
                -- Preserve invalidation for published history even after source
                -- revisions have cascaded away on deletion. This is a PK lookup
                -- for one entity, never a kind-wide expansion in the write path.
                FOR history IN EXECUTE format(
                    'SELECT snapshot_key FROM core.%I WHERE entity_id=$1 '
                    'UNION SELECT snapshot_key FROM ops.read_projection_dirty '
                    'WHERE kind=$2 AND entity_id=$1 ORDER BY 1', p_kind || '_read')
                    USING p_entity, p_kind
                LOOP
                    IF history <> 'current' THEN
                        PERFORM ops.mark_read_projection(p_kind, p_entity, history);
                    END IF;
                END LOOP;
            END IF;
        END;
        $$
    """)
    op.execute("""
        CREATE FUNCTION ops.mark_read_node(p_node bigint, p_release bigint DEFAULT NULL)
        RETURNS void LANGUAGE plpgsql AS $$
        DECLARE k text;
        BEGIN
            SELECT CASE t.key WHEN 'area' THEN 'location'
                             WHEN 'dialogue_line' THEN 'dialogue'
                             WHEN 'talk_item' THEN 'dialogue'
                             WHEN 'narration' THEN 'dialogue'
                             WHEN 'player_choice' THEN 'dialogue'
                             WHEN 'phone_message' THEN 'dialogue' ELSE t.key END INTO k
            FROM graph.node n JOIN graph.node_type t ON t.id = n.type_id WHERE n.id = p_node;
            IF k IN ('character','item','location','quest','speaker','dialogue','cutscene') THEN
                PERFORM ops.mark_read_projection(k, p_node);
                IF p_release IS NOT NULL AND k NOT IN ('location','speaker') THEN
                    PERFORM ops.mark_read_projection(k, p_node, 'release:' || p_release);
                END IF;
            END IF;
        END;
        $$
    """)
    # Row-local identities retain deletes and old/new owners; no entity-table
    # expansion happens in these write transactions.
    op.execute("""
        CREATE FUNCTION ops.read_entity_changed() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE d jsonb; r bigint; download text; event_identity bigint; version text; event_ids jsonb;
                speaker bigint; linked_entity bigint;
        BEGIN
            FOR d IN SELECT value FROM jsonb_array_elements(
                CASE TG_OP WHEN 'INSERT' THEN jsonb_build_array(to_jsonb(NEW))
                           WHEN 'DELETE' THEN jsonb_build_array(to_jsonb(OLD))
                           ELSE jsonb_build_array(to_jsonb(OLD), to_jsonb(NEW)) END)
            LOOP
                PERFORM ops.mark_read_projection(TG_ARGV[0], (d ->> TG_ARGV[1])::bigint);
                IF TG_ARGV[0] = 'quest' THEN
                    PERFORM ops.mark_read_projection('story_map');
                END IF;
                IF TG_ARGV[0] IN ('speaker','dialogue') THEN
                    speaker := CASE WHEN TG_ARGV[0] = 'speaker' THEN (d ->> TG_ARGV[1])::bigint
                                    ELSE (d ->> 'speaker_node_id')::bigint END;
                    IF speaker IS NOT NULL THEN
                        PERFORM ops.mark_read_node(speaker);
                        FOR linked_entity IN SELECT entity_node_id FROM core.speaker_entity_link
                            WHERE speaker_node_id = speaker ORDER BY entity_node_id
                        LOOP
                            PERFORM ops.mark_read_node(linked_entity);
                        END LOOP;
                    END IF;
                END IF;
                r := (d ->> 'release_id')::bigint;
                IF r IS NOT NULL THEN
                    PERFORM ops.mark_read_projection(TG_ARGV[0], (d ->> TG_ARGV[1])::bigint, 'release:' || r);
                END IF;
                version := d ->> 'game_version';
                IF TG_ARGV[0] = 'game_event' AND version IS NOT NULL THEN
                    PERFORM ops.mark_read_projection('game_event', (d ->> TG_ARGV[1])::bigint, version);
                END IF;
                IF TG_ARGV[0] = 'game_notice' THEN
                    event_ids := d -> 'related_event_ids';
                    IF event_ids IS NULL THEN
                        SELECT related_event_ids INTO event_ids FROM core.game_notice
                        WHERE id = (d ->> TG_ARGV[1])::bigint;
                    END IF;
                    FOR event_identity IN SELECT value::bigint FROM jsonb_array_elements_text(
                        CASE WHEN jsonb_typeof(event_ids) = 'array' THEN event_ids ELSE '[]'::jsonb END)
                    LOOP
                        PERFORM ops.mark_read_projection('game_event', event_identity);
                        FOR version IN SELECT DISTINCT game_version FROM core.game_event_occurrence
                            WHERE event_id = event_identity AND game_version IS NOT NULL
                        LOOP
                            PERFORM ops.mark_read_projection('game_event', event_identity, version);
                        END LOOP;
                    END LOOP;
                END IF;
                download := d #>> '{source_data,client,download_id}';
                IF TG_ARGV[0] = 'game_event' AND download IS NOT NULL AND download <> '' THEN
                    PERFORM ops.mark_read_projection(TG_ARGV[0], 0, 'download:' || download);
                END IF;
            END LOOP;
            RETURN NULL;
        END;
        $$
    """)
    for table, (kind, identity) in DIRECT_TABLES.items():
        op.execute(f"CREATE TRIGGER read_projection_changed AFTER INSERT OR UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION ops.read_entity_changed('{kind}', '{identity}')")
    op.execute("""
        CREATE FUNCTION ops.read_node_changed() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE d jsonb; field text; k text; identity bigint; owner record;
        BEGIN
            FOR d IN SELECT value FROM jsonb_array_elements(
                CASE TG_OP WHEN 'INSERT' THEN jsonb_build_array(to_jsonb(NEW))
                           WHEN 'DELETE' THEN jsonb_build_array(to_jsonb(OLD))
                           ELSE jsonb_build_array(to_jsonb(OLD), to_jsonb(NEW)) END)
            LOOP
                IF TG_TABLE_SCHEMA = 'graph' AND TG_TABLE_NAME = 'node' THEN
                    SELECT CASE key WHEN 'area' THEN 'location'
                                    WHEN 'dialogue_line' THEN 'dialogue'
                                    WHEN 'talk_item' THEN 'dialogue'
                                    WHEN 'narration' THEN 'dialogue'
                                    WHEN 'player_choice' THEN 'dialogue'
                                    WHEN 'phone_message' THEN 'dialogue' ELSE key END INTO k
                    FROM graph.node_type WHERE id = (d ->> 'type_id')::smallint;
                    IF k IN ('character','item','location','quest','speaker','dialogue','cutscene') THEN
                        PERFORM ops.mark_read_projection(k, (d ->> 'id')::bigint);
                        IF k = 'quest' THEN PERFORM ops.mark_read_projection('story_map'); END IF;
                    END IF;
                ELSIF TG_TABLE_SCHEMA = 'content' AND TG_TABLE_NAME = 'document_reference' THEN
                    SELECT node_id INTO identity FROM content.document WHERE id=(d->>'document_id')::bigint;
                    IF identity IS NOT NULL THEN PERFORM ops.mark_read_node(identity); END IF;
                ELSIF TG_TABLE_SCHEMA = 'graph' AND TG_TABLE_NAME = 'edge_evidence' THEN
                    FOR owner IN SELECT from_node_id,to_node_id FROM graph.edge WHERE id=(d->>'edge_id')::bigint
                    LOOP
                        PERFORM ops.mark_read_node(owner.from_node_id, (d->>'release_id')::bigint);
                        PERFORM ops.mark_read_node(owner.to_node_id, (d->>'release_id')::bigint);
                    END LOOP;
                ELSE
                    FOREACH field IN ARRAY TG_ARGV LOOP
                        identity := (d ->> field)::bigint;
                        IF identity IS NOT NULL THEN
                            PERFORM ops.mark_read_node(identity, (d ->> 'release_id')::bigint);
                        END IF;
                    END LOOP;
                END IF;
            END LOOP;
            RETURN NULL;
        END;
        $$
    """)
    for table, fields in NODE_TABLES.items():
        arguments = ",".join(f"'{field}'" for field in fields)
        op.execute(f"CREATE TRIGGER read_projection_changed AFTER INSERT OR UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION ops.read_node_changed({arguments})")
    # Transition tables deduplicate file owners per statement. Location/object
    # changes resolve only indexed references for the changed file IDs.
    op.execute("""
        CREATE FUNCTION ops.read_storage_changed() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE changes text; query text; ref record;
        BEGIN
            changes := CASE TG_OP WHEN 'INSERT' THEN 'SELECT * FROM new_rows'
                                 WHEN 'DELETE' THEN 'SELECT * FROM old_rows'
                                 ELSE 'SELECT * FROM old_rows UNION SELECT * FROM new_rows' END;
            IF TG_ARGV[0] = 'reference' THEN
                query := 'SELECT DISTINCT owner_node_id, release_id, metadata->>''download_id'' AS download_id, reference_type FROM (' || changes || ') c ORDER BY owner_node_id, release_id, download_id, reference_type';
            ELSE
                query := 'SELECT DISTINCT r.owner_node_id, r.release_id, r.metadata->>''download_id'' AS download_id, r.reference_type '
                    || 'FROM storage.file_reference r JOIN (SELECT DISTINCT ' || quote_ident(TG_ARGV[0])
                    || ' AS file_id FROM (' || changes || ') c) f ON f.file_id = r.file_id ORDER BY r.owner_node_id, r.release_id, download_id, r.reference_type';
            END IF;
            FOR ref IN EXECUTE query LOOP
                IF ref.owner_node_id IS NOT NULL THEN
                    PERFORM ops.mark_read_node(ref.owner_node_id, ref.release_id);
                ELSIF ref.download_id IS NOT NULL THEN
                    PERFORM ops.mark_read_projection('game_event', 0, 'download:' || ref.download_id);
                ELSIF ref.reference_type = 'notice_image' THEN
                    PERFORM ops.mark_read_projection('game_notice');
                    PERFORM ops.mark_read_projection('game_event');
                END IF;
            END LOOP;
            RETURN NULL;
        END;
        $$
    """)
    for table, field in STORAGE_TABLES.items():
        for action, transition in (
            ("INSERT", "NEW TABLE AS new_rows"),
            ("UPDATE", "OLD TABLE AS old_rows NEW TABLE AS new_rows"),
            ("DELETE", "OLD TABLE AS old_rows"),
        ):
            op.execute(f"CREATE TRIGGER read_projection_{action.lower()} AFTER {action} ON {table} REFERENCING {transition} FOR EACH STATEMENT EXECUTE FUNCTION ops.read_storage_changed('{field}')")
    # Shared dependency tables get only a bounded set of kind markers. Storage
    # and directly owned rows are deliberately excluded from this coarse path.
    excluded = tuple(DIRECT_TABLES) + tuple(STORAGE_TABLES) + MEDIA_TABLES + tuple(NODE_TABLES) + READ_TABLES + ANALYSIS_ONLY_TABLES
    excluded_sql = ",".join(f"'{table}'" for table in excluded)
    kinds_sql = ",".join(f"'{kind}'" for kind in KINDS)
    op.execute(f"""
        CREATE FUNCTION ops.read_dependency_changed() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE k text; snapshot text;
        BEGIN
            IF NEW.table_name IN ({excluded_sql}) THEN RETURN NULL; END IF;
            IF split_part(NEW.table_name, '.', 1) NOT IN ('core','graph','raw','i18n','search','ontology','story','content')
               AND NEW.table_name NOT IN ('ops.game_release','ops.import_run') THEN RETURN NULL; END IF;
            FOREACH k IN ARRAY ARRAY[{kinds_sql}] LOOP
                PERFORM ops.mark_read_projection(k);
                IF k NOT IN ('location','speaker','game_notice') THEN
                    FOR snapshot IN SELECT snapshot_key FROM ops.read_projection_scope
                        WHERE kind=k AND snapshot_key <> 'current'
                          AND snapshot_key NOT LIKE 'download:%' ORDER BY snapshot_key
                    LOOP
                        PERFORM ops.mark_read_projection(k, 0, snapshot);
                    END LOOP;
                END IF;
            END LOOP;
            RETURN NULL;
        END;
        $$
    """)
    op.execute("CREATE TRIGGER read_projection_dependency_changed AFTER INSERT OR UPDATE ON ops.public_cache_revision FOR EACH ROW EXECUTE FUNCTION ops.read_dependency_changed()")
    op.execute("""
        CREATE FUNCTION ops.read_source_truncated() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE k text; snapshot text;
        BEGIN
            FOREACH k IN ARRAY TG_ARGV LOOP
                PERFORM ops.mark_read_projection(k);
                IF k NOT IN ('location','speaker','game_notice') THEN
                    FOR snapshot IN SELECT snapshot_key FROM ops.read_projection_scope
                        WHERE kind=k AND snapshot_key <> 'current'
                          AND snapshot_key NOT LIKE 'download:%' ORDER BY snapshot_key
                    LOOP
                        PERFORM ops.mark_read_projection(k, 0, snapshot);
                    END LOOP;
                END IF;
            END LOOP;
            RETURN NULL;
        END;
        $$
    """)
    for table in (*DIRECT_TABLES, *NODE_TABLES, *STORAGE_TABLES):
        kinds = (DIRECT_TABLES[table][0],) if table in DIRECT_TABLES else KINDS
        if table in STORAGE_TABLES or table in MEDIA_TABLES:
            kinds = tuple(kind for kind in kinds if kind != "story_map")
        arguments = ",".join(f"'{kind}'" for kind in kinds)
        op.execute(f"CREATE TRIGGER read_projection_truncated AFTER TRUNCATE ON {table} FOR EACH STATEMENT EXECUTE FUNCTION ops.read_source_truncated({arguments})")
    # Read-table writes invalidate HTTP responses but never dirty their sources.
    # On fresh installs 0008 may already have attached these triggers.
    for table in READ_TABLES:
        op.execute(f"INSERT INTO ops.public_cache_revision VALUES ('{table}', txid_current()) ON CONFLICT DO NOTHING")
        op.execute(f"DROP TRIGGER IF EXISTS public_cache_changed ON {table}")
        op.execute(f"CREATE TRIGGER public_cache_changed AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON {table} FOR EACH STATEMENT EXECUTE FUNCTION ops.bump_public_cache_revision()")
    # 0008 enumerated tables only once. Later source tables need a revision
    # trigger too; use our own name so downgrade removes only what we added.
    op.execute("""
        DO $$ DECLARE t record; BEGIN
            FOR t IN SELECT c.oid, n.nspname AS schema_name, c.relname AS table_name
                FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE c.relkind = 'r' AND
                    (n.nspname IN ('core','graph','raw','i18n','search','ontology','story','content','storage')
                     OR (n.nspname = 'ops' AND c.relname IN ('game_release','import_run')))
                AND NOT EXISTS (SELECT 1 FROM pg_trigger g WHERE g.tgrelid=c.oid
                    AND g.tgfoid='ops.bump_public_cache_revision()'::regprocedure
                    AND g.tgenabled IN ('O','A'))
                ORDER BY n.nspname, c.relname
            LOOP
                EXECUTE format('CREATE TRIGGER read_projection_source_revision '
                    'AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON %I.%I '
                    'FOR EACH STATEMENT EXECUTE FUNCTION ops.bump_public_cache_revision()',
                    t.schema_name, t.table_name);
            END LOOP;
        END $$
    """)
    for kind in KINDS:
        op.execute(f"SELECT ops.mark_read_projection('{kind}')")


def downgrade():
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.execute("DROP TRIGGER read_projection_dependency_changed ON ops.public_cache_revision")
    op.execute("DROP FUNCTION ops.read_dependency_changed()")
    op.execute("""
        DO $$ DECLARE t record; BEGIN
            FOR t IN SELECT n.nspname, c.relname FROM pg_trigger g
                JOIN pg_class c ON c.oid=g.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE g.tgname='read_projection_source_revision'
                  AND g.tgfoid='ops.bump_public_cache_revision()'::regprocedure
            LOOP
                EXECUTE format('DROP TRIGGER read_projection_source_revision ON %I.%I', t.nspname, t.relname);
            END LOOP;
        END $$
    """)
    for table in (*DIRECT_TABLES, *NODE_TABLES, *STORAGE_TABLES):
        op.execute(f"DROP TRIGGER read_projection_truncated ON {table}")
    op.execute("DROP FUNCTION ops.read_source_truncated()")
    for table in (*DIRECT_TABLES, *NODE_TABLES):
        op.execute(f"DROP TRIGGER read_projection_changed ON {table}")
    for table in STORAGE_TABLES:
        for action in ("insert", "update", "delete"):
            op.execute(f"DROP TRIGGER read_projection_{action} ON {table}")
    op.execute("DROP FUNCTION ops.read_storage_changed()")
    op.execute("DROP FUNCTION ops.read_node_changed()")
    op.execute("DROP FUNCTION ops.read_entity_changed()")
    op.execute("DROP FUNCTION ops.mark_read_node(bigint, bigint)")
    op.execute("DROP FUNCTION ops.mark_read_projection(text, bigint, text)")
    for table in reversed(READ_TABLES):
        op.execute(f"DELETE FROM ops.public_cache_revision WHERE table_name = '{table}'")
        schema, name = table.split(".")
        op.drop_table(name, schema=schema)
    op.drop_table("read_projection_dirty", schema="ops")
    op.execute("DELETE FROM ops.public_cache_revision WHERE table_name='ops.read_projection_scope'")
    op.drop_table("read_projection_scope", schema="ops")
