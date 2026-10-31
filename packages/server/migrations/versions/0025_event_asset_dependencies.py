"""Stage per-event asset invalidation; backfill and activation are explicit."""

import re

import sqlalchemy as sa
from alembic import op

revision = "0025_event_asset_dependencies"
down_revision = "0024_browse_read_models"
branch_labels = None
depends_on = None

OLD_MARK = "PERFORM ops.mark_read_projection(TG_ARGV[0], (d ->> TG_ARGV[1])::bigint);"
NEW_MARK = """IF TG_ARGV[0] = 'game_event' AND (SELECT active FROM ops.event_asset_state WHERE id=1) THEN
                    PERFORM ops.mark_event_asset_changed((d ->> TG_ARGV[1])::bigint, d ->> 'game_version');
                ELSE
                    PERFORM ops.mark_read_projection(TG_ARGV[0], (d ->> TG_ARGV[1])::bigint);
                END IF;"""
OLD_DOWNLOAD = "PERFORM ops.mark_read_projection(TG_ARGV[0], 0, 'download:' || download);"
NEW_DOWNLOAD = """IF NOT (SELECT active FROM ops.event_asset_state WHERE id=1) THEN
                        PERFORM ops.mark_read_projection(TG_ARGV[0], 0, 'download:' || download);
                    END IF;"""


def _entity_function(forward):
    definition = op.get_bind().scalar(
        sa.text("SELECT pg_get_functiondef('ops.read_entity_changed()'::regprocedure)")
    )
    for old, new in ((OLD_MARK, NEW_MARK), (OLD_DOWNLOAD, NEW_DOWNLOAD)):
        source, target = (old, new) if forward else (new, old)
        if definition.count(source) != 1:
            raise RuntimeError("Unexpected read_entity_changed definition; migration aborted")
        definition = definition.replace(source, target)
    op.execute(definition)


def upgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    statements = r"""
        CREATE TABLE ops.event_asset_state (
            id smallint PRIMARY KEY CHECK(id=1), active boolean NOT NULL DEFAULT false
        );
        INSERT INTO ops.event_asset_state(id) VALUES(1);
        CREATE TABLE ops.event_asset_coverage (
            event_id bigint PRIMARY KEY REFERENCES core.game_event(id) ON DELETE CASCADE,
            covered boolean NOT NULL DEFAULT false
        );
        CREATE TABLE ops.event_asset_dependency (
            event_id bigint NOT NULL REFERENCES core.game_event(id) ON DELETE CASCADE,
            download_id text NOT NULL, asset_key text NOT NULL,
            PRIMARY KEY(event_id, download_id, asset_key)
        );
        CREATE INDEX ix_event_asset_download_key_event
            ON ops.event_asset_dependency(download_id, asset_key, event_id);
        CREATE FUNCTION ops.event_asset_key(path text) RETURNS text
        LANGUAGE plpgsql IMMUTABLE STRICT AS $$
        DECLARE value text;
        BEGIN
            value := replace(btrim(path), chr(92), '/');
            value := regexp_replace(value, '^[^'']*''([^'']+)''$', '\1');
            value := regexp_replace(value, '[?#].*$', '');
            value := split_part(regexp_replace(value, '^.*/', ''), '.', 1);
            value := lower(btrim(value));
            IF value = '' OR value !~ '^[[:alnum:]_-]+$' THEN RETURN NULL; END IF;
            RETURN md5(value);
        END; $$;
        CREATE FUNCTION ops.event_asset_patch(version text) RETURNS numeric[]
        LANGUAGE sql IMMUTABLE STRICT AS $$
            SELECT CASE WHEN version ~ '^[0-9]+[.][0-9]+([.][0-9]+)*$'
                THEN ARRAY[split_part(version,'.',1)::numeric, split_part(version,'.',2)::numeric]
                ELSE NULL END
        $$;
        CREATE FUNCTION ops.mark_event_asset_changed(identity bigint, version text DEFAULT NULL)
        RETURNS void LANGUAGE plpgsql AS $$
        DECLARE snapshot text;
        BEGIN
            PERFORM ops.mark_read_projection('game_event', identity);
            FOR snapshot IN SELECT DISTINCT s.snapshot_key FROM ops.read_projection_scope s
                WHERE s.kind='game_event' AND s.snapshot_key <> 'current'
                  AND s.snapshot_key NOT LIKE 'download:%'
                  AND (ops.event_asset_patch(s.snapshot_key)=ops.event_asset_patch(version)
                       OR EXISTS (SELECT 1 FROM core.game_event_occurrence o
                           WHERE o.event_id=identity
                             AND ops.event_asset_patch(o.game_version)=ops.event_asset_patch(s.snapshot_key)))
                ORDER BY s.snapshot_key
            LOOP
                PERFORM ops.mark_read_projection('game_event', identity, snapshot);
            END LOOP;
        END; $$;
        CREATE FUNCTION ops.refresh_event_asset_dependencies(identity bigint) RETURNS void
        LANGUAGE plpgsql AS $$
        BEGIN
            IF NOT pg_try_advisory_xact_lock(705,25) THEN
                RAISE EXCEPTION 'Event asset ordering conflict; retry transaction' USING ERRCODE='40001';
            END IF;
            -- Keep the parent alive, then serialize all source writes/backfills
            -- through one row. The upsert also fences repeatable-read writers.
            PERFORM 1 FROM core.game_event WHERE id=identity FOR KEY SHARE;
            IF NOT FOUND THEN RETURN; END IF;
            INSERT INTO ops.event_asset_coverage(event_id,covered) VALUES(identity,false)
                ON CONFLICT(event_id) DO UPDATE SET covered=false;
            DELETE FROM ops.event_asset_dependency WHERE event_id=identity;
            WITH header AS MATERIALIZED (
                SELECT id, banner_path,
                    CASE WHEN jsonb_typeof(source_data->'client')='object'
                         THEN source_data->'client' ELSE '{}'::jsonb END AS client
                FROM core.game_event WHERE id=identity AND source='wuwa-game-client'
            ), sources AS MATERIALIZED (
                SELECT client, banner_path FROM header
                UNION ALL
                SELECT CASE WHEN coalesce(h.client->>'download_id','') <> ''
                                  AND h.client->>'download_id'=o.source_data#>>'{client,download_id}'
                            THEN h.client
                            WHEN jsonb_typeof(o.source_data->'client')='object'
                                 AND o.source_data->'client' <> '{}'::jsonb
                            THEN o.source_data->'client' ELSE h.client END,
                       coalesce(nullif(o.banner_path,''),h.banner_path)
                FROM header h JOIN core.game_event_occurrence o ON o.event_id=h.id
            ), assets AS (
                SELECT coalesce(client->>'download_id','') AS download_id, banner_path AS path
                FROM sources WHERE banner_path IS NOT NULL AND banner_path <> ''
                UNION ALL
                SELECT coalesce(s.client->>'download_id',''),
                       CASE WHEN jsonb_typeof(a.value->'path')='string' THEN a.value->>'path' END
                FROM sources s CROSS JOIN (VALUES('artwork'),('prefab_artwork')) field(name)
                CROSS JOIN LATERAL jsonb_array_elements(
                    CASE WHEN jsonb_typeof(s.client->field.name)='array'
                         THEN s.client->field.name ELSE '[]'::jsonb END) a(value)
                UNION ALL
                SELECT coalesce(s.client->>'download_id',''), NULL
                FROM sources s CROSS JOIN (VALUES('artwork'),('prefab_artwork')) field(name)
                WHERE s.client ? field.name AND jsonb_typeof(s.client->field.name) NOT IN ('array','null')
            )
            INSERT INTO ops.event_asset_dependency(event_id,download_id,asset_key)
                SELECT DISTINCT identity, download_id, coalesce(ops.event_asset_key(path),'*') FROM assets;
            UPDATE ops.event_asset_coverage SET covered=true WHERE event_id=identity;
        END; $$;
        CREATE FUNCTION ops.register_event_asset_source() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            -- A caller may already hold source/storage row locks. Never wait
            -- here: controlled rollback/retry prevents gate/row-lock cycles.
            IF NOT pg_try_advisory_xact_lock(705,25) THEN
                RAISE EXCEPTION 'Event asset ordering conflict; retry transaction' USING ERRCODE='40001';
            END IF;
            IF TG_TABLE_SCHEMA='storage' AND current_setting('transaction_isolation') NOT IN ('read committed','read uncommitted') THEN
                RAISE EXCEPTION 'Event asset publication requires READ COMMITTED isolation' USING ERRCODE='40001';
            END IF;
            RETURN NULL;
        END; $$;
        CREATE TRIGGER read_event_asset_registration BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE ON core.game_event
            FOR EACH STATEMENT EXECUTE FUNCTION ops.register_event_asset_source();
        CREATE TRIGGER read_event_asset_registration BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE ON core.game_event_occurrence
            FOR EACH STATEMENT EXECUTE FUNCTION ops.register_event_asset_source();
        CREATE TRIGGER read_event_asset_registration BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE ON storage.file_reference
            FOR EACH STATEMENT EXECUTE FUNCTION ops.register_event_asset_source();
        CREATE TRIGGER read_event_asset_registration BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE ON storage.file_object
            FOR EACH STATEMENT EXECUTE FUNCTION ops.register_event_asset_source();
        CREATE TRIGGER read_event_asset_registration BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE ON storage.file_location
            FOR EACH STATEMENT EXECUTE FUNCTION ops.register_event_asset_source();
        CREATE TRIGGER read_event_asset_registration BEFORE UPDATE ON ops.event_asset_state
            FOR EACH STATEMENT EXECUTE FUNCTION ops.register_event_asset_source();
        CREATE FUNCTION ops.read_event_asset_source_changed() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE identity bigint; identities bigint[];
        BEGIN
            IF TG_TABLE_NAME='game_event' THEN
                identities := CASE TG_OP WHEN 'INSERT' THEN ARRAY[NEW.id]
                    WHEN 'DELETE' THEN ARRAY[OLD.id] ELSE ARRAY[OLD.id,NEW.id] END;
            ELSE
                identities := CASE TG_OP WHEN 'INSERT' THEN ARRAY[NEW.event_id]
                    WHEN 'DELETE' THEN ARRAY[OLD.event_id] ELSE ARRAY[OLD.event_id,NEW.event_id] END;
            END IF;
            FOR identity IN SELECT DISTINCT value FROM unnest(identities) value ORDER BY value LOOP
                PERFORM ops.refresh_event_asset_dependencies(identity);
            END LOOP;
            RETURN NULL;
        END; $$;
        CREATE TRIGGER read_event_asset_bindings AFTER INSERT OR UPDATE OR DELETE ON core.game_event
            FOR EACH ROW EXECUTE FUNCTION ops.read_event_asset_source_changed();
        CREATE TRIGGER read_event_asset_bindings AFTER INSERT OR UPDATE OR DELETE ON core.game_event_occurrence
            FOR EACH ROW EXECUTE FUNCTION ops.read_event_asset_source_changed();
        CREATE FUNCTION ops.guard_event_asset_activation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.active AND NOT OLD.active THEN
                IF NOT pg_try_advisory_xact_lock(705,25) THEN
                    RAISE EXCEPTION 'Event asset ordering conflict; retry transaction' USING ERRCODE='40001';
                END IF;
                IF EXISTS (SELECT 1 FROM core.game_event e WHERE NOT EXISTS (
                    SELECT 1 FROM ops.event_asset_coverage c WHERE c.event_id=e.id AND c.covered)) THEN
                    RAISE EXCEPTION 'Event asset coverage incomplete';
                END IF;
            END IF;
            RETURN NEW;
        END; $$;
        CREATE TRIGGER guard_event_asset_activation BEFORE UPDATE ON ops.event_asset_state
            FOR EACH ROW EXECUTE FUNCTION ops.guard_event_asset_activation();
    """
    for statement in re.split(r";\s*(?=CREATE |INSERT INTO ops\.event_asset_state)", statements):
        op.execute(statement)
    _entity_function(True)
    op.execute(STORAGE_FUNCTION)


STORAGE_FUNCTION = """
    CREATE OR REPLACE FUNCTION ops.read_storage_changed() RETURNS trigger LANGUAGE plpgsql VOLATILE AS $$
    DECLARE changes text; query text; ref record; enabled boolean; path_key text; name_key text;
            identity bigint; ids bigint[] := '{}'; downloads text[] := '{}'; download text;
            previous_owner bigint; previous_release bigint; notice_fallback boolean := false;
    BEGIN
        -- Fence every reference lookup, including blank wildcard/name-only
        -- matches, before reading activation state. Reentrant after BEFORE DML.
        IF NOT pg_try_advisory_xact_lock(705,25) THEN
            RAISE EXCEPTION 'Event asset ordering conflict; retry transaction' USING ERRCODE='40001';
        END IF;
        IF current_setting('transaction_isolation') NOT IN ('read committed','read uncommitted') THEN
            RAISE EXCEPTION 'Event asset publication requires READ COMMITTED isolation' USING ERRCODE='40001';
        END IF;
        SELECT active INTO enabled FROM ops.event_asset_state WHERE id=1;
        changes := CASE TG_OP WHEN 'INSERT' THEN 'SELECT * FROM new_rows'
                             WHEN 'DELETE' THEN 'SELECT * FROM old_rows'
                             ELSE 'SELECT * FROM old_rows UNION SELECT * FROM new_rows' END;
        IF TG_ARGV[0]='reference' THEN
            query := 'SELECT DISTINCT owner_node_id,release_id,metadata->>''download_id'' AS download_id,reference_type,source_path,source_name FROM ('||changes||') c';
        ELSE
            query := 'SELECT DISTINCT r.owner_node_id,r.release_id,r.metadata->>''download_id'' AS download_id,r.reference_type,r.source_path,r.source_name '
                ||'FROM storage.file_reference r JOIN (SELECT DISTINCT '||quote_ident(TG_ARGV[0])
                ||' AS file_id FROM ('||changes||') c) f ON f.file_id=r.file_id';
        END IF;
        query := query || ' ORDER BY owner_node_id,release_id,download_id,reference_type,source_path,source_name';
        FOR ref IN EXECUTE query LOOP
            IF ref.owner_node_id IS NOT NULL AND (ref.owner_node_id IS DISTINCT FROM previous_owner
                                                  OR ref.release_id IS DISTINCT FROM previous_release) THEN
                PERFORM ops.mark_read_node(ref.owner_node_id,ref.release_id);
                previous_owner := ref.owner_node_id; previous_release := ref.release_id;
            END IF;
            IF enabled THEN
                path_key := ops.event_asset_key(ref.source_path);
                name_key := ops.event_asset_key(ref.source_name);
                IF ref.owner_node_id IS NULL AND ref.reference_type='notice_image' AND ref.download_id IS NOT NULL THEN
                    -- Notice URLs are not represented by client artwork paths.
                    -- Preserve the pre-existing capture fallback for these refs.
                    downloads := array_append(downloads,ref.download_id);
                ELSIF ref.owner_node_id IS NULL AND path_key IS NULL
                      AND nullif(btrim(ref.source_path),'') IS NOT NULL
                      AND ref.download_id IS NOT NULL THEN
                    downloads := array_append(downloads,ref.download_id);
                ELSE
                    ids := ids || ARRAY(SELECT DISTINCT d.event_id FROM ops.event_asset_dependency d
                        WHERE d.download_id=coalesce(ref.download_id,'')
                          AND d.asset_key IN (path_key,name_key,'*'));
                END IF;
            ELSIF ref.owner_node_id IS NULL AND ref.download_id IS NOT NULL THEN
                downloads := array_append(downloads,ref.download_id);
            END IF;
            IF ref.owner_node_id IS NULL AND ref.download_id IS NULL AND ref.reference_type='notice_image' THEN
                notice_fallback := true;
            END IF;
        END LOOP;
        FOR identity IN SELECT DISTINCT value FROM unnest(ids) value ORDER BY value LOOP
            PERFORM ops.mark_event_asset_changed(identity);
        END LOOP;
        FOR download IN SELECT DISTINCT value FROM unnest(downloads) value ORDER BY value LOOP
            PERFORM ops.mark_read_projection('game_event',0,'download:'||download);
        END LOOP;
        IF notice_fallback THEN
            PERFORM ops.mark_read_projection('game_notice');
            PERFORM ops.mark_read_projection('game_event');
        END IF;
        RETURN NULL;
    END; $$
"""


def downgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    _entity_function(False)
    op.execute(OLD_STORAGE_FUNCTION)
    op.execute("DROP TRIGGER guard_event_asset_activation ON ops.event_asset_state")
    op.execute("DROP TRIGGER read_event_asset_registration ON ops.event_asset_state")
    op.execute("DROP FUNCTION ops.guard_event_asset_activation()")
    for table in ("file_reference", "file_object", "file_location"):
        op.execute(f"DROP TRIGGER read_event_asset_registration ON storage.{table}")
    for table in ("game_event_occurrence", "game_event"):
        op.execute(f"DROP TRIGGER read_event_asset_bindings ON core.{table}")
        op.execute(f"DROP TRIGGER read_event_asset_registration ON core.{table}")
    op.execute("DROP FUNCTION ops.register_event_asset_source()")
    op.execute("DROP FUNCTION ops.read_event_asset_source_changed()")
    op.execute("DROP FUNCTION ops.refresh_event_asset_dependencies(bigint)")
    op.execute("DROP FUNCTION ops.mark_event_asset_changed(bigint,text)")
    op.execute("DROP FUNCTION ops.event_asset_patch(text)")
    op.execute("DROP FUNCTION ops.event_asset_key(text)")
    for table in ("event_asset_dependency", "event_asset_coverage", "event_asset_state"):
        op.execute(f"DROP TABLE ops.{table}")


OLD_STORAGE_FUNCTION = """
    CREATE OR REPLACE FUNCTION ops.read_storage_changed() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE changes text; query text; ref record;
    BEGIN
        changes := CASE TG_OP WHEN 'INSERT' THEN 'SELECT * FROM new_rows'
                             WHEN 'DELETE' THEN 'SELECT * FROM old_rows'
                             ELSE 'SELECT * FROM old_rows UNION SELECT * FROM new_rows' END;
        IF TG_ARGV[0]='reference' THEN
            query := 'SELECT DISTINCT owner_node_id,release_id,metadata->>''download_id'' AS download_id,reference_type FROM ('||changes||') c ORDER BY owner_node_id,release_id,download_id,reference_type';
        ELSE
            query := 'SELECT DISTINCT r.owner_node_id,r.release_id,r.metadata->>''download_id'' AS download_id,r.reference_type '
                ||'FROM storage.file_reference r JOIN (SELECT DISTINCT '||quote_ident(TG_ARGV[0])
                ||' AS file_id FROM ('||changes||') c) f ON f.file_id=r.file_id ORDER BY r.owner_node_id,r.release_id,download_id,r.reference_type';
        END IF;
        FOR ref IN EXECUTE query LOOP
            IF ref.owner_node_id IS NOT NULL THEN PERFORM ops.mark_read_node(ref.owner_node_id,ref.release_id);
            ELSIF ref.download_id IS NOT NULL THEN PERFORM ops.mark_read_projection('game_event',0,'download:'||ref.download_id);
            ELSIF ref.reference_type='notice_image' THEN
                PERFORM ops.mark_read_projection('game_notice'); PERFORM ops.mark_read_projection('game_event');
            END IF;
        END LOOP;
        RETURN NULL;
    END; $$
"""
