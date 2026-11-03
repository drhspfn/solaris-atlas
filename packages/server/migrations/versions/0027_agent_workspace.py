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
        "CREATE TABLE ops.agent_memory (\n\tid BIGINT GENERATED ALWAYS AS IDENTITY, \n\tkey VARCHAR(160) NOT NULL, \n\tscope VARCHAR(16) NOT NULL, \n\trun_id BIGINT, \n\tnode_id BIGINT, \n\ttext TEXT NOT NULL, \n\tcitations JSONB NOT NULL, \n\trevision INTEGER NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tCONSTRAINT pk_agent_memory PRIMARY KEY (id), \n\tCONSTRAINT uq_agent_memory_key UNIQUE (key), \n\tCONSTRAINT fk_agent_memory_run_id_processing_run FOREIGN KEY(run_id) REFERENCES ops.processing_run (id) ON DELETE CASCADE, \n\tCONSTRAINT fk_agent_memory_node_id_node FOREIGN KEY(node_id) REFERENCES graph.node (id) ON DELETE CASCADE\n)"
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


def downgrade():
    op.drop_table("agent_work_item", schema="ops")
    op.drop_table("agent_memory_embedding", schema="search")
    op.drop_table("agent_memory", schema="ops")
    op.drop_table("agent_tool_audit", schema="ops")
