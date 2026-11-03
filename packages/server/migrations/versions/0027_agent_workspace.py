"""Durable agent audit, searchable memory and cross-quest work ledger."""

from alembic import op

revision = "0027_agent_workspace"
down_revision = "0026_event_publication_locks"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute(
        "CREATE TABLE ops.agent_tool_audit (\n\tid BIGINT GENERATED ALWAYS AS IDENTITY, \n\trequest_id VARCHAR(64) NOT NULL, \n\trole VARCHAR(24) NOT NULL, \n\trun_id BIGINT, \n\tstep INTEGER, \n\tcall_id VARCHAR(256) NOT NULL, \n\ttool VARCHAR(128) NOT NULL, \n\tstatus VARCHAR(24) NOT NULL, \n\targuments JSONB NOT NULL, \n\tresult JSONB NOT NULL, \n\tduration_ms INTEGER, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tCONSTRAINT pk_agent_tool_audit PRIMARY KEY (id), \n\tCONSTRAINT fk_agent_tool_audit_run_id_processing_run FOREIGN KEY(run_id) REFERENCES ops.processing_run (id) ON DELETE SET NULL\n)"
    )
    op.execute("CREATE INDEX ix_agent_tool_audit_created_at ON ops.agent_tool_audit (created_at)")
    op.execute("CREATE INDEX ix_agent_tool_audit_request_id ON ops.agent_tool_audit (request_id)")
    op.execute("CREATE INDEX ix_agent_tool_audit_run_id ON ops.agent_tool_audit (run_id)")
    op.execute(
        "CREATE TABLE ops.agent_memory (\n\tid BIGINT GENERATED ALWAYS AS IDENTITY, \n\tkey VARCHAR(160) NOT NULL, \n\tscope VARCHAR(16) NOT NULL, \n\trun_id BIGINT, \n\tnode_id BIGINT, \n\ttext TEXT NOT NULL, \n\tcitations JSONB NOT NULL, \n\trevision INTEGER NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tCONSTRAINT pk_agent_memory PRIMARY KEY (id), \n\tCONSTRAINT uq_agent_memory_key UNIQUE (key), \n\tCONSTRAINT fk_agent_memory_run_id_processing_run FOREIGN KEY(run_id) REFERENCES ops.processing_run (id) ON DELETE SET NULL, \n\tCONSTRAINT fk_agent_memory_node_id_node FOREIGN KEY(node_id) REFERENCES graph.node (id) ON DELETE CASCADE\n)"
    )
    op.execute("CREATE INDEX ix_agent_memory_node_id ON ops.agent_memory (node_id)")
    op.execute("CREATE INDEX ix_agent_memory_run_id ON ops.agent_memory (run_id)")
    op.execute("CREATE INDEX ix_agent_memory_scope ON ops.agent_memory (scope)")
    op.execute(
        "CREATE TABLE search.agent_memory_embedding (\n\tmemory_id BIGINT NOT NULL, \n\tmodel_id BIGINT NOT NULL, \n\tembedding VECTOR NOT NULL, \n\tcontent_hash VARCHAR(64) NOT NULL, \n\tCONSTRAINT pk_agent_memory_embedding PRIMARY KEY (memory_id, model_id), \n\tCONSTRAINT fk_agent_memory_embedding_memory_id_agent_memory FOREIGN KEY(memory_id) REFERENCES ops.agent_memory (id) ON DELETE CASCADE, \n\tCONSTRAINT fk_agent_memory_embedding_model_id_embedding_model FOREIGN KEY(model_id) REFERENCES search.embedding_model (id) ON DELETE CASCADE\n)"
    )
    op.execute(
        "CREATE TABLE ops.agent_work_item (\n\tid BIGINT GENERATED ALWAYS AS IDENTITY, \n\tkey VARCHAR(200) NOT NULL, \n\tkind VARCHAR(32) NOT NULL, \n\tnode_id BIGINT NOT NULL, \n\tdocument_id BIGINT, \n\trun_id BIGINT, \n\tpriority INTEGER NOT NULL, \n\tstatus VARCHAR(24) NOT NULL, \n\ttitle VARCHAR(512) NOT NULL, \n\ttext TEXT NOT NULL, \n\tsearch_terms JSONB NOT NULL, \n\tcitations JSONB NOT NULL, \n\tresolution JSONB NOT NULL, \n\trevision INTEGER NOT NULL, \n\tattempted_corpus VARCHAR(64), \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tCONSTRAINT pk_agent_work_item PRIMARY KEY (id), \n\tCONSTRAINT uq_agent_work_item_key UNIQUE (key), \n\tCONSTRAINT fk_agent_work_item_node_id_node FOREIGN KEY(node_id) REFERENCES graph.node (id) ON DELETE CASCADE, \n\tCONSTRAINT fk_agent_work_item_document_id_document FOREIGN KEY(document_id) REFERENCES content.document (id) ON DELETE CASCADE, \n\tCONSTRAINT fk_agent_work_item_run_id_processing_run FOREIGN KEY(run_id) REFERENCES ops.processing_run (id) ON DELETE SET NULL\n)"
    )
    op.execute("CREATE INDEX ix_agent_work_item_document_id ON ops.agent_work_item (document_id)")
    op.execute("CREATE INDEX ix_agent_work_item_kind ON ops.agent_work_item (kind)")
    op.execute("CREATE INDEX ix_agent_work_item_node_id ON ops.agent_work_item (node_id)")
    op.execute("CREATE INDEX ix_agent_work_item_priority ON ops.agent_work_item (priority)")
    op.execute("CREATE INDEX ix_agent_work_item_run_id ON ops.agent_work_item (run_id)")
    op.execute("CREATE INDEX ix_agent_work_item_status ON ops.agent_work_item (status)")
    op.execute(
        "CREATE INDEX ix_agent_memory_text ON ops.agent_memory USING gin (to_tsvector('simple', text))"
    )
    op.execute(
        "CREATE INDEX ix_agent_work_item_text ON ops.agent_work_item USING gin (to_tsvector('simple', text))"
    )

    # Existing published questions become searchable work without rewriting documents.
    op.execute("""
        INSERT INTO ops.agent_work_item
            (key,kind,node_id,document_id,priority,status,title,text,search_terms,citations,resolution,revision)
        SELECT 'question:'||d.node_id||':'||d.locale_id||':'||(d.metadata->>'release_id')||':'||(h.value->>'key'),
            'question',d.node_id,d.id,
            CASE h.value->>'priority' WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 WHEN 'low' THEN 3 ELSE 4 END,
            'open',left(h.value->>'question',512),h.value->>'question',
            coalesce(h.value->'search_terms','[]'::jsonb),coalesce(h.value->'citations','[]'::jsonb),'{}'::jsonb,1
        FROM content.document d JOIN content.document_head head ON head.document_id=d.id
        CROSS JOIN LATERAL jsonb_array_elements(CASE WHEN jsonb_typeof(d.metadata->'hooks')='array' THEN d.metadata->'hooks' ELSE '[]'::jsonb END) h(value)
        WHERE d.document_type LIKE 'story-explanation:%' AND d.locale_id IS NOT NULL
            AND d.metadata->>'release_id' IS NOT NULL AND h.value->>'key' IS NOT NULL
        ON CONFLICT (key) DO NOTHING
    """)
    op.execute("""
        INSERT INTO ops.agent_work_item
            (key,kind,node_id,document_id,priority,status,title,text,search_terms,citations,resolution,revision)
        SELECT 'legacy-question:'||d.id||':'||q.ordinality,'question',d.node_id,d.id,2,'open',
            left(q.value,512),q.value,'[]'::jsonb,'[]'::jsonb,'{}'::jsonb,1
        FROM content.document d JOIN content.document_head head ON head.document_id=d.id
        CROSS JOIN LATERAL jsonb_array_elements_text(CASE WHEN jsonb_typeof(d.metadata->'unresolved_questions')='array' THEN d.metadata->'unresolved_questions' ELSE '[]'::jsonb END) WITH ORDINALITY q(value,ordinality)
        WHERE d.document_type LIKE 'story-explanation:%' AND length(q.value)>0
            AND NOT EXISTS (SELECT 1 FROM ops.agent_work_item w WHERE w.document_id=d.id AND w.text=q.value)
        ON CONFLICT (key) DO NOTHING
    """)
    op.execute(
        "INSERT INTO ops.public_cache_revision VALUES ('ops.agent_work_item',txid_current()) ON CONFLICT DO NOTHING"
    )
    op.execute(
        "CREATE TRIGGER public_cache_changed AFTER UPDATE OF resolution ON ops.agent_work_item FOR EACH STATEMENT EXECUTE FUNCTION ops.bump_public_cache_revision()"
    )


def downgrade():
    op.execute("DELETE FROM ops.public_cache_revision WHERE table_name='ops.agent_work_item'")
    op.drop_table("agent_work_item", schema="ops")
    op.drop_table("agent_memory_embedding", schema="search")
    op.drop_table("agent_memory", schema="ops")
    op.drop_table("agent_tool_audit", schema="ops")
