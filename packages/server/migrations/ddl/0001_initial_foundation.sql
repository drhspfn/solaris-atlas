-- Frozen foundation DDL from dd6b612; independent of runtime ORM metadata.

-- statement
CREATE EXTENSION IF NOT EXISTS vector;

-- statement
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- statement
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- statement
CREATE SCHEMA IF NOT EXISTS "ops";

-- statement
CREATE SCHEMA IF NOT EXISTS "storage";

-- statement
CREATE SCHEMA IF NOT EXISTS "raw";

-- statement
CREATE SCHEMA IF NOT EXISTS "i18n";

-- statement
CREATE SCHEMA IF NOT EXISTS "graph";

-- statement
CREATE SCHEMA IF NOT EXISTS "core";

-- statement
CREATE SCHEMA IF NOT EXISTS "ontology";

-- statement
CREATE SCHEMA IF NOT EXISTS "story";

-- statement
CREATE SCHEMA IF NOT EXISTS "content";

-- statement
CREATE SCHEMA IF NOT EXISTS "search";

-- statement
CREATE SCHEMA IF NOT EXISTS "auth";

-- statement
CREATE TABLE auth."user" (
	id BIGINT GENERATED ALWAYS AS IDENTITY,
	email TEXT NOT NULL,
	email_normalized TEXT NOT NULL,
	nickname VARCHAR(32) NOT NULL,
	nickname_normalized VARCHAR(32) NOT NULL,
	password_hash TEXT NOT NULL,
	role SMALLINT DEFAULT '1' NOT NULL,
	status SMALLINT DEFAULT '1' NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	last_login_at TIMESTAMP WITH TIME ZONE,
	CONSTRAINT pk_user PRIMARY KEY (id),
	CONSTRAINT uq_user_email_normalized UNIQUE (email_normalized),
	CONSTRAINT uq_user_nickname_normalized UNIQUE (nickname_normalized),
	CONSTRAINT ck_user_role_valid CHECK (role IN (1, 2)),
	CONSTRAINT ck_user_status_valid CHECK (status IN (1, 2))
);

-- statement
CREATE TABLE auth.pending_registration (
	id BIGINT GENERATED ALWAYS AS IDENTITY,
	token_hash BYTEA NOT NULL,
	provider SMALLINT NOT NULL,
	provider_subject TEXT NOT NULL,
	email TEXT NOT NULL,
	email_normalized TEXT NOT NULL,
	suggested_nickname VARCHAR(32) NOT NULL,
	metadata JSONB DEFAULT '{}'::jsonb NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	consumed_at TIMESTAMP WITH TIME ZONE,
	CONSTRAINT pk_pending_registration PRIMARY KEY (id),
	CONSTRAINT uq_pending_registration_token_hash UNIQUE (token_hash)
);

-- statement
CREATE INDEX ix_pending_registration_expires_at ON auth.pending_registration (expires_at);

-- statement
CREATE TABLE graph.node_type (
	id SMALLSERIAL NOT NULL,
	key VARCHAR(64) NOT NULL,
	description TEXT,
	CONSTRAINT pk_node_type PRIMARY KEY (id),
	CONSTRAINT uq_node_type_key UNIQUE (key)
);

-- statement
CREATE TABLE i18n.locale (
	id SMALLSERIAL NOT NULL,
	code VARCHAR(16) NOT NULL,
	name VARCHAR(128) NOT NULL,
	CONSTRAINT pk_locale PRIMARY KEY (id),
	CONSTRAINT uq_locale_code UNIQUE (code)
);

-- statement
CREATE TABLE ontology.relation_type (
	id SMALLSERIAL NOT NULL,
	key VARCHAR(64) NOT NULL,
	label VARCHAR(128) NOT NULL,
	description TEXT,
	inverse_relation_id SMALLINT,
	category VARCHAR(64) DEFAULT 'general' NOT NULL,
	directional BOOLEAN DEFAULT 'true' NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	metadata JSONB DEFAULT '{}'::jsonb NOT NULL,
	CONSTRAINT pk_relation_type PRIMARY KEY (id),
	CONSTRAINT uq_relation_type_key UNIQUE (key),
	CONSTRAINT fk_relation_type_inverse_relation_id_relation_type FOREIGN KEY(inverse_relation_id) REFERENCES ontology.relation_type (id) ON DELETE SET NULL
);

-- statement
CREATE TABLE ontology.tag (
	id BIGINT GENERATED ALWAYS AS IDENTITY,
	key VARCHAR(128) NOT NULL,
	label VARCHAR(256) NOT NULL,
	description TEXT,
	metadata JSONB DEFAULT '{}'::jsonb NOT NULL,
	CONSTRAINT pk_tag PRIMARY KEY (id),
	CONSTRAINT uq_tag_key UNIQUE (key)
);

-- statement
CREATE TABLE ontology.event_type (
	id SMALLSERIAL NOT NULL,
	key VARCHAR(64) NOT NULL,
	label VARCHAR(128) NOT NULL,
	description TEXT,
	CONSTRAINT pk_event_type PRIMARY KEY (id),
	CONSTRAINT uq_event_type_key UNIQUE (key)
);

-- statement
CREATE TABLE ops.game_release (
	id BIGINT GENERATED ALWAYS AS IDENTITY,
	sequence INTEGER NOT NULL,
	game_version VARCHAR(64) NOT NULL,
	resource_version VARCHAR(64),
	changelist BIGINT,
	upstream_name VARCHAR(256) NOT NULL,
	upstream_commit VARCHAR(128),
	released_at TIMESTAMP WITH TIME ZONE,
	imported_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	metadata JSONB DEFAULT '{}'::jsonb NOT NULL,
	CONSTRAINT pk_game_release PRIMARY KEY (id),
	CONSTRAINT uq_release_identity UNIQUE NULLS NOT DISTINCT (game_version, resource_version, upstream_name, upstream_commit),
	CONSTRAINT uq_game_release_sequence UNIQUE (sequence)
);

-- statement
CREATE INDEX ix_game_release_game_version ON ops.game_release (game_version);

-- statement
CREATE TABLE ops.processor (
	id BIGINT GENERATED ALWAYS AS IDENTITY,
	key VARCHAR(128) NOT NULL,
	version VARCHAR(64) NOT NULL,
	description TEXT,
	metadata JSONB DEFAULT '{}'::jsonb NOT NULL,
	CONSTRAINT pk_processor PRIMARY KEY (id),
	CONSTRAINT uq_processor_key UNIQUE (key)
);

-- statement
CREATE TABLE ops.ai_model (
	id BIGINT GENERATED ALWAYS AS IDENTITY,
	provider VARCHAR(128) NOT NULL,
	model_name VARCHAR(256) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	metadata JSONB DEFAULT '{}'::jsonb NOT NULL,
	CONSTRAINT pk_ai_model PRIMARY KEY (id)
);

-- statement
CREATE TABLE search.embedding_model (
	id BIGINT GENERATED ALWAYS AS IDENTITY,
	key VARCHAR(128) NOT NULL,
	provider VARCHAR(128) NOT NULL,
	model_name VARCHAR(256) NOT NULL,
	dimensions INTEGER NOT NULL,
	distance_metric VARCHAR(32) NOT NULL,
	config JSONB NOT NULL,
	active BOOLEAN DEFAULT 'false' NOT NULL,
	CONSTRAINT pk_embedding_model PRIMARY KEY (id),
	CONSTRAINT uq_embedding_model_key UNIQUE (key)
);

-- statement
CREATE TABLE ops.status_type (
	category VARCHAR(32) NOT NULL,
	key VARCHAR(32) NOT NULL,
	terminal BOOLEAN DEFAULT 'false' NOT NULL,
	description TEXT,
	CONSTRAINT pk_status_type PRIMARY KEY (category, key)
);

-- statement
CREATE TABLE storage.file_type (
	id SMALLSERIAL NOT NULL,
	key VARCHAR(64) NOT NULL,
	description VARCHAR(256),
	CONSTRAINT pk_file_type PRIMARY KEY (id),
	CONSTRAINT uq_file_type_key UNIQUE (key)
);

-- statement
CREATE TABLE auth.session (
	id BIGINT GENERATED ALWAYS AS IDENTITY,
	user_id BIGINT NOT NULL,
	token_hash BYTEA NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	last_seen_at TIMESTAMP WITH TIME ZONE NOT NULL,
	revoked_at TIMESTAMP WITH TIME ZONE,
	ip_address INET,
	user_agent TEXT,
	CONSTRAINT pk_session PRIMARY KEY (id),
	CONSTRAINT fk_session_user_id_user FOREIGN KEY(user_id) REFERENCES auth."user" (id) ON DELETE CASCADE,
	CONSTRAINT uq_session_token_hash UNIQUE (token_hash)
);

-- statement
CREATE INDEX ix_session_user_id ON auth.session (user_id);

-- statement
CREATE INDEX ix_session_expires_at ON auth.session (expires_at);

-- statement
CREATE TABLE auth.identity (
	id BIGINT GENERATED ALWAYS AS IDENTITY,
	user_id BIGINT NOT NULL,
	provider SMALLINT NOT NULL,
	provider_subject TEXT NOT NULL,
	provider_email TEXT,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	metadata JSONB DEFAULT '{}'::jsonb NOT NULL,
	CONSTRAINT pk_identity PRIMARY KEY (id),
	CONSTRAINT uq_identity_provider UNIQUE (provider, provider_subject),
	CONSTRAINT ck_identity_provider_google CHECK (provider = 1),
	CONSTRAINT fk_identity_user_id_user FOREIGN KEY(user_id) REFERENCES auth."user" (id) ON DELETE CASCADE
);

-- statement
CREATE INDEX ix_identity_user_id ON auth.identity (user_id);

-- statement
CREATE TABLE auth.pending_link (
	id BIGINT GENERATED ALWAYS AS IDENTITY,
	token_hash BYTEA NOT NULL,
	user_id BIGINT NOT NULL,
	provider SMALLINT NOT NULL,
	provider_subject TEXT NOT NULL,
	provider_email TEXT NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	consumed_at TIMESTAMP WITH TIME ZONE,
	CONSTRAINT pk_pending_link PRIMARY KEY (id),
	CONSTRAINT uq_pending_link_token_hash UNIQUE (token_hash),
	CONSTRAINT fk_pending_link_user_id_user FOREIGN KEY(user_id) REFERENCES auth."user" (id) ON DELETE CASCADE
);

-- statement
CREATE INDEX ix_pending_link_expires_at ON auth.pending_link (expires_at);

-- statement
CREATE TABLE graph.node (
	id BIGINT GENERATED ALWAYS AS IDENTITY,
	type_id SMALLINT NOT NULL,
	canonical_key VARCHAR(512) NOT NULL,
	slug VARCHAR(512),
	created_release_id BIGINT,
	status VARCHAR(32) DEFAULT 'active' NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	metadata JSONB DEFAULT '{}'::jsonb NOT NULL,
	CONSTRAINT pk_node PRIMARY KEY (id),
	CONSTRAINT fk_node_type_id_node_type FOREIGN KEY(type_id) REFERENCES graph.node_type (id) ON DELETE RESTRICT,
	CONSTRAINT fk_node_created_release_id_game_release FOREIGN KEY(created_release_id) REFERENCES ops.game_release (id) ON DELETE SET NULL
);

-- statement
CREATE INDEX ix_node_slug ON graph.node (slug);

-- statement
CREATE INDEX ix_node_type_id ON graph.node (type_id);

-- statement
CREATE UNIQUE INDEX ix_node_canonical_key ON graph.node (canonical_key);

-- statement
CREATE TABLE i18n.localization_key (
	id BIGINT GENERATED ALWAYS AS IDENTITY,
	key VARCHAR(1024) NOT NULL,
	namespace VARCHAR(128),
	first_release_id BIGINT,
	metadata JSONB DEFAULT '{}'::jsonb NOT NULL,
	CONSTRAINT pk_localization_key PRIMARY KEY (id),
	CONSTRAINT uq_localization_key_key UNIQUE (key),
	CONSTRAINT fk_localization_key_first_release_id_game_release FOREIGN KEY(first_release_id) REFERENCES ops.game_release (id) ON DELETE SET NULL
);

-- statement
CREATE TABLE ontology.relation_alias (
	id BIGINT GENERATED ALWAYS AS IDENTITY,
	relation_type_id SMALLINT NOT NULL,
	locale_id SMALLINT,
	alias VARCHAR(256) NOT NULL,
	normalized_alias VARCHAR(256) NOT NULL,
	CONSTRAINT pk_relation_alias PRIMARY KEY (id),
	CONSTRAINT uq_relation_alias UNIQUE NULLS NOT DISTINCT (relation_type_id, locale_id, normalized_alias),
	CONSTRAINT fk_relation_alias_relation_type_id_relation_type FOREIGN KEY(relation_type_id) REFERENCES ontology.relation_type (id) ON DELETE CASCADE,
	CONSTRAINT fk_relation_alias_locale_id_locale FOREIGN KEY(locale_id) REFERENCES i18n.locale (id) ON DELETE CASCADE
);

-- statement
CREATE INDEX ix_relation_alias_normalized_alias ON ontology.relation_alias (normalized_alias);

-- statement
CREATE TABLE ontology.tag_alias (
	id BIGINT GENERATED ALWAYS AS IDENTITY,
	tag_id BIGINT NOT NULL,
	locale_id SMALLINT,
	alias VARCHAR(256) NOT NULL,
	normalized_alias VARCHAR(256) NOT NULL,
	CONSTRAINT pk_tag_alias PRIMARY KEY (id),
	CONSTRAINT uq_tag_alias UNIQUE NULLS NOT DISTINCT (tag_id, locale_id, normalized_alias),
	CONSTRAINT fk_tag_alias_tag_id_tag FOREIGN KEY(tag_id) REFERENCES ontology.tag (id) ON DELETE CASCADE,
	CONSTRAINT fk_tag_alias_locale_id_locale FOREIGN KEY(locale_id) REFERENCES i18n.locale (id) ON DELETE CASCADE
);

-- statement
CREATE INDEX ix_tag_alias_normalized_alias ON ontology.tag_alias (normalized_alias);

-- statement
CREATE TABLE ops.import_run (
	id BIGINT GENERATED ALWAYS AS IDENTITY,
	release_id BIGINT NOT NULL,
	importer VARCHAR(128) NOT NULL,
	importer_version VARCHAR(64) NOT NULL,
	started_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	finished_at TIMESTAMP WITH TIME ZONE,
	status VARCHAR(24) DEFAULT 'pending' NOT NULL,
	records_seen BIGINT DEFAULT '0' NOT NULL,
	records_created BIGINT DEFAULT '0' NOT NULL,
	records_updated BIGINT DEFAULT '0' NOT NULL,
	records_failed BIGINT DEFAULT '0' NOT NULL,
	error TEXT,
	metadata JSONB DEFAULT '{}'::jsonb NOT NULL,
	CONSTRAINT pk_import_run PRIMARY KEY (id),
	CONSTRAINT fk_import_run_release_id_game_release FOREIGN KEY(release_id) REFERENCES ops.game_release (id) ON DELETE RESTRICT
);

-- statement
CREATE INDEX ix_import_run_release_id ON ops.import_run (release_id);

-- statement
CREATE TABLE search.relation_embedding (
	id BIGINT GENERATED ALWAYS AS IDENTITY,
	relation_type_id SMALLINT NOT NULL,
	model_id BIGINT NOT NULL,
	content_hash BYTEA NOT NULL,
	embedding VECTOR NOT NULL,
	CONSTRAINT pk_relation_embedding PRIMARY KEY (id),
	CONSTRAINT uq_relation_embedding UNIQUE (relation_type_id, model_id),
	CONSTRAINT fk_relation_embedding_relation_type_id_relation_type FOREIGN KEY(relation_type_id) REFERENCES ontology.relation_type (id) ON DELETE CASCADE,
	CONSTRAINT fk_relation_embedding_model_id_embedding_model FOREIGN KEY(model_id) REFERENCES search.embedding_model (id) ON DELETE RESTRICT
);

-- statement
CREATE TABLE search.tag_embedding (
	id BIGINT GENERATED ALWAYS AS IDENTITY,
	tag_id BIGINT NOT NULL,
	model_id BIGINT NOT NULL,
	content_hash BYTEA NOT NULL,
	embedding VECTOR NOT NULL,
	CONSTRAINT pk_tag_embedding PRIMARY KEY (id),
	CONSTRAINT uq_tag_embedding UNIQUE (tag_id, model_id),
	CONSTRAINT fk_tag_embedding_tag_id_tag FOREIGN KEY(tag_id) REFERENCES ontology.tag (id) ON DELETE CASCADE,
	CONSTRAINT fk_tag_embedding_model_id_embedding_model FOREIGN KEY(model_id) REFERENCES search.embedding_model (id) ON DELETE RESTRICT
);

-- statement
CREATE TABLE storage.file_object (
	id BIGINT GENERATED ALWAYS AS IDENTITY,
	file_type_id SMALLINT NOT NULL,
	logical_name VARCHAR(1024),
	mime_type VARCHAR(255),
	size_bytes BIGINT,
	sha256 BYTEA,
	etag VARCHAR(256),
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	metadata JSONB DEFAULT '{}'::jsonb NOT NULL,
	CONSTRAINT pk_file_object PRIMARY KEY (id),
	CONSTRAINT ck_file_object_nonnegative_size CHECK (size_bytes IS NULL OR size_bytes >= 0),
	CONSTRAINT fk_file_object_file_type_id_file_type FOREIGN KEY(file_type_id) REFERENCES storage.file_type (id) ON DELETE RESTRICT
);

-- statement
CREATE UNIQUE INDEX ix_file_object_sha256 ON storage.file_object (sha256);

-- statement
CREATE TABLE auth.oauth_state (
	id BIGINT GENERATED ALWAYS AS IDENTITY,
	token_hash BYTEA NOT NULL,
	mode VARCHAR(16) NOT NULL,
	user_id BIGINT,
	session_id BIGINT,
	nonce TEXT NOT NULL,
	code_verifier TEXT NOT NULL,
	return_to TEXT DEFAULT '/' NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	consumed_at TIMESTAMP WITH TIME ZONE,
	CONSTRAINT pk_oauth_state PRIMARY KEY (id),
	CONSTRAINT ck_oauth_state_mode_valid CHECK (mode IN ('login', 'link')),
	CONSTRAINT uq_oauth_state_token_hash UNIQUE (token_hash),
	CONSTRAINT fk_oauth_state_user_id_user FOREIGN KEY(user_id) REFERENCES auth."user" (id) ON DELETE CASCADE,
	CONSTRAINT fk_oauth_state_session_id_session FOREIGN KEY(session_id) REFERENCES auth.session (id) ON DELETE CASCADE
);

-- statement
CREATE INDEX ix_oauth_state_expires_at ON auth.oauth_state (expires_at);

-- statement
CREATE TABLE core.character (
	node_id BIGINT NOT NULL,
	canonical_name VARCHAR(256),
	name_key_id BIGINT,
	nickname_key_id BIGINT,
	description TEXT,
	playable BOOLEAN,
	metadata JSONB DEFAULT '{}'::jsonb NOT NULL,
	CONSTRAINT pk_character PRIMARY KEY (node_id),
	CONSTRAINT fk_character_node_id_node FOREIGN KEY(node_id) REFERENCES graph.node (id) ON DELETE CASCADE,
	CONSTRAINT fk_character_name_key_id_localization_key FOREIGN KEY(name_key_id) REFERENCES i18n.localization_key (id) ON DELETE SET NULL,
	CONSTRAINT fk_character_nickname_key_id_localization_key FOREIGN KEY(nickname_key_id) REFERENCES i18n.localization_key (id) ON DELETE SET NULL
);

-- statement
CREATE TABLE core.organization (
	node_id BIGINT NOT NULL,
	canonical_name VARCHAR(256),
	description TEXT,
	metadata JSONB DEFAULT '{}'::jsonb NOT NULL,
	CONSTRAINT pk_organization PRIMARY KEY (node_id),
	CONSTRAINT fk_organization_node_id_node FOREIGN KEY(node_id) REFERENCES graph.node (id) ON DELETE CASCADE
);

-- statement
CREATE TABLE core.location (
	node_id BIGINT NOT NULL,
	canonical_name VARCHAR(256),
	name_key_id BIGINT,
	game_location_id VARCHAR(256),
	parent_location_node_id BIGINT,
	metadata JSONB DEFAULT '{}'::jsonb NOT NULL,
	CONSTRAINT pk_location PRIMARY KEY (node_id),
	CONSTRAINT fk_location_node_id_node FOREIGN KEY(node_id) REFERENCES graph.node (id) ON DELETE CASCADE,
	CONSTRAINT fk_location_name_key_id_localization_key FOREIGN KEY(name_key_id) REFERENCES i18n.localization_key (id) ON DELETE SET NULL,
	CONSTRAINT fk_location_parent_location_node_id_location FOREIGN KEY(parent_location_node_id) REFERENCES core.location (node_id) ON DELETE SET NULL
);

-- statement
CREATE INDEX ix_location_game_location_id ON core.location (game_location_id);

-- statement
CREATE TABLE core.item (
	node_id BIGINT NOT NULL,
	canonical_name VARCHAR(256),
	name_key_id BIGINT,
	description_key_id BIGINT,
	description TEXT,
	game_item_id BIGINT,
	metadata JSONB DEFAULT '{}'::jsonb NOT NULL,
	CONSTRAINT pk_item PRIMARY KEY (node_id),
	CONSTRAINT uq_core_item_game_id UNIQUE (game_item_id),
	CONSTRAINT fk_item_node_id_node FOREIGN KEY(node_id) REFERENCES graph.node (id) ON DELETE CASCADE,
	CONSTRAINT fk_item_name_key_id_localization_key FOREIGN KEY(name_key_id) REFERENCES i18n.localization_key (id) ON DELETE SET NULL,
	CONSTRAINT fk_item_description_key_id_localization_key FOREIGN KEY(description_key_id) REFERENCES i18n.localization_key (id) ON DELETE SET NULL
);

-- statement
CREATE TABLE core.term (
	node_id BIGINT NOT NULL,
	canonical_name VARCHAR(256),
	description TEXT,
	metadata JSONB DEFAULT '{}'::jsonb NOT NULL,
	CONSTRAINT pk_term PRIMARY KEY (node_id),
	CONSTRAINT fk_term_node_id_node FOREIGN KEY(node_id) REFERENCES graph.node (id) ON DELETE CASCADE
);

-- statement
CREATE TABLE core.concept (
	node_id BIGINT NOT NULL,
	canonical_name VARCHAR(256),
	description TEXT,
	metadata JSONB DEFAULT '{}'::jsonb NOT NULL,
	CONSTRAINT pk_concept PRIMARY KEY (node_id),
	CONSTRAINT fk_concept_node_id_node FOREIGN KEY(node_id) REFERENCES graph.node (id) ON DELETE CASCADE
);

-- statement
CREATE TABLE core.speaker (
	node_id BIGINT NOT NULL,
	game_speaker_id BIGINT NOT NULL,
	name_key_id BIGINT,
	metadata JSONB DEFAULT '{}'::jsonb NOT NULL,
	CONSTRAINT pk_speaker PRIMARY KEY (node_id),
	CONSTRAINT fk_speaker_node_id_node FOREIGN KEY(node_id) REFERENCES graph.node (id) ON DELETE CASCADE,
	CONSTRAINT uq_speaker_game_speaker_id UNIQUE (game_speaker_id),
	CONSTRAINT fk_speaker_name_key_id_localization_key FOREIGN KEY(name_key_id) REFERENCES i18n.localization_key (id) ON DELETE SET NULL
);

-- statement
CREATE TABLE core.quest (
	node_id BIGINT NOT NULL,
	game_quest_id BIGINT NOT NULL,
	name_key_id BIGINT,
	description_key_id BIGINT,
	quest_type VARCHAR(64),
	region_id VARCHAR(128),
	metadata JSONB DEFAULT '{}'::jsonb NOT NULL,
	CONSTRAINT pk_quest PRIMARY KEY (node_id),
	CONSTRAINT uq_core_quest_game_id UNIQUE (game_quest_id),
	CONSTRAINT fk_quest_node_id_node FOREIGN KEY(node_id) REFERENCES graph.node (id) ON DELETE CASCADE,
	CONSTRAINT fk_quest_name_key_id_localization_key FOREIGN KEY(name_key_id) REFERENCES i18n.localization_key (id) ON DELETE SET NULL,
	CONSTRAINT fk_quest_description_key_id_localization_key FOREIGN KEY(description_key_id) REFERENCES i18n.localization_key (id) ON DELETE SET NULL
);

-- statement
CREATE INDEX ix_quest_game_quest_id ON core.quest (game_quest_id);

-- statement
CREATE TABLE core.media_asset (
	node_id BIGINT NOT NULL,
	media_kind VARCHAR(32) NOT NULL,
	original_file_id BIGINT,
	preferred_file_id BIGINT,
	duration_ms BIGINT,
	width INTEGER,
	height INTEGER,
	bitrate INTEGER,
	metadata JSONB DEFAULT '{}'::jsonb NOT NULL,
	CONSTRAINT pk_media_asset PRIMARY KEY (node_id),
	CONSTRAINT fk_media_asset_node_id_node FOREIGN KEY(node_id) REFERENCES graph.node (id) ON DELETE CASCADE,
	CONSTRAINT fk_media_asset_original_file_id_file_object FOREIGN KEY(original_file_id) REFERENCES storage.file_object (id) ON DELETE SET NULL,
	CONSTRAINT fk_media_asset_preferred_file_id_file_object FOREIGN KEY(preferred_file_id) REFERENCES storage.file_object (id) ON DELETE SET NULL
);

-- statement
CREATE TABLE ops.processing_run (
	id BIGINT GENERATED ALWAYS AS IDENTITY,
	processor_id BIGINT NOT NULL,
	model_id BIGINT,
	target_node_id BIGINT,
	prompt_version VARCHAR(64),
	input_hash BYTEA,
	status VARCHAR(24) DEFAULT 'pending' NOT NULL,
	tokens_input BIGINT,
	tokens_output BIGINT,
	cost DOUBLE PRECISION,
	raw_output JSONB,
	started_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	finished_at TIMESTAMP WITH TIME ZONE,
	error TEXT,
	metadata JSONB DEFAULT '{}'::jsonb NOT NULL,
	CONSTRAINT pk_processing_run PRIMARY KEY (id),
	CONSTRAINT fk_processing_run_processor_id_processor FOREIGN KEY(processor_id) REFERENCES ops.processor (id) ON DELETE RESTRICT,
	CONSTRAINT fk_processing_run_model_id_ai_model FOREIGN KEY(model_id) REFERENCES ops.ai_model (id) ON DELETE SET NULL,
	CONSTRAINT fk_processing_run_target_node_id_node FOREIGN KEY(target_node_id) REFERENCES graph.node (id) ON DELETE SET NULL
);

-- statement
CREATE TABLE ops.dependency (
	from_node_id BIGINT NOT NULL,
	to_node_id BIGINT NOT NULL,
	dependency_type VARCHAR(64) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_dependency PRIMARY KEY (from_node_id, to_node_id, dependency_type),
	CONSTRAINT fk_dependency_from_node_id_node FOREIGN KEY(from_node_id) REFERENCES graph.node (id) ON DELETE CASCADE,
	CONSTRAINT fk_dependency_to_node_id_node FOREIGN KEY(to_node_id) REFERENCES graph.node (id) ON DELETE CASCADE
);

-- statement
CREATE TABLE raw.source_file (
	id BIGINT GENERATED ALWAYS AS IDENTITY,
	release_id BIGINT NOT NULL,
	file_object_id BIGINT,
	logical_source_path VARCHAR(2048) NOT NULL,
	table_name VARCHAR(512),
	schema_hash BYTEA,
	row_count BIGINT,
	metadata JSONB DEFAULT '{}'::jsonb NOT NULL,
	CONSTRAINT pk_source_file PRIMARY KEY (id),
	CONSTRAINT uq_source_file_release_path UNIQUE (release_id, logical_source_path),
	CONSTRAINT fk_source_file_release_id_game_release FOREIGN KEY(release_id) REFERENCES ops.game_release (id) ON DELETE RESTRICT,
	CONSTRAINT fk_source_file_file_object_id_file_object FOREIGN KEY(file_object_id) REFERENCES storage.file_object (id) ON DELETE SET NULL
);

-- statement
CREATE INDEX ix_source_file_release_id ON raw.source_file (release_id);

-- statement
CREATE TABLE search.entity_alias (
	id BIGINT GENERATED ALWAYS AS IDENTITY,
	node_id BIGINT NOT NULL,
	locale_id SMALLINT,
	alias VARCHAR(512) NOT NULL,
	normalized_alias VARCHAR(512) NOT NULL,
	alias_type VARCHAR(32) NOT NULL,
	source VARCHAR(64) NOT NULL,
	CONSTRAINT pk_entity_alias PRIMARY KEY (id),
	CONSTRAINT uq_entity_alias UNIQUE (node_id, locale_id, normalized_alias, alias_type),
	CONSTRAINT fk_entity_alias_node_id_node FOREIGN KEY(node_id) REFERENCES graph.node (id) ON DELETE CASCADE,
	CONSTRAINT fk_entity_alias_locale_id_locale FOREIGN KEY(locale_id) REFERENCES i18n.locale (id) ON DELETE CASCADE
);

-- statement
CREATE INDEX ix_entity_alias_trgm ON search.entity_alias USING gin (normalized_alias gin_trgm_ops);

-- statement
CREATE INDEX ix_entity_alias_node_id ON search.entity_alias (node_id);

-- statement
CREATE INDEX ix_entity_alias_normalized_alias ON search.entity_alias (normalized_alias);

-- statement
CREATE TABLE search.document (
	id BIGINT GENERATED ALWAYS AS IDENTITY,
	target_node_id BIGINT NOT NULL,
	category VARCHAR(64) NOT NULL,
	locale_id SMALLINT,
	title TEXT NOT NULL,
	aliases JSONB NOT NULL,
	body TEXT NOT NULL,
	search_vector TSVECTOR,
	content_hash BYTEA NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_document PRIMARY KEY (id),
	CONSTRAINT uq_search_document UNIQUE NULLS NOT DISTINCT (target_node_id, category, locale_id),
	CONSTRAINT fk_document_target_node_id_node FOREIGN KEY(target_node_id) REFERENCES graph.node (id) ON DELETE CASCADE,
	CONSTRAINT fk_document_locale_id_locale FOREIGN KEY(locale_id) REFERENCES i18n.locale (id) ON DELETE CASCADE
);

-- statement
CREATE INDEX ix_search_document_fts ON search.document USING gin (search_vector);

-- statement
CREATE INDEX ix_search_document_title_trgm ON search.document USING gin (title gin_trgm_ops);

-- statement
CREATE INDEX ix_document_target_node_id ON search.document (target_node_id);

-- statement
CREATE TABLE search.node_embedding (
	id BIGINT GENERATED ALWAYS AS IDENTITY,
	node_id BIGINT NOT NULL,
	embedding_kind VARCHAR(32) NOT NULL,
	locale_id SMALLINT,
	model_id BIGINT NOT NULL,
	content_hash BYTEA NOT NULL,
	embedding VECTOR NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_node_embedding PRIMARY KEY (id),
	CONSTRAINT uq_node_embedding UNIQUE NULLS NOT DISTINCT (node_id, embedding_kind, locale_id, model_id),
	CONSTRAINT fk_node_embedding_node_id_node FOREIGN KEY(node_id) REFERENCES graph.node (id) ON DELETE CASCADE,
	CONSTRAINT fk_node_embedding_locale_id_locale FOREIGN KEY(locale_id) REFERENCES i18n.locale (id) ON DELETE CASCADE,
	CONSTRAINT fk_node_embedding_model_id_embedding_model FOREIGN KEY(model_id) REFERENCES search.embedding_model (id) ON DELETE RESTRICT
);

-- statement
CREATE INDEX ix_node_embedding_node_id ON search.node_embedding (node_id);

-- statement
CREATE TABLE storage.file_location (
	id BIGINT GENERATED ALWAYS AS IDENTITY,
	file_id BIGINT NOT NULL,
	backend VARCHAR(32) NOT NULL,
	bucket VARCHAR(255),
	object_key VARCHAR(2048) NOT NULL,
	region VARCHAR(128),
	storage_class VARCHAR(64),
	etag VARCHAR(256),
	is_primary BOOLEAN DEFAULT 'false' NOT NULL,
	available BOOLEAN DEFAULT 'true' NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	verified_at TIMESTAMP WITH TIME ZONE,
	metadata JSONB DEFAULT '{}'::jsonb NOT NULL,
	CONSTRAINT pk_file_location PRIMARY KEY (id),
	CONSTRAINT uq_file_location_identity UNIQUE NULLS NOT DISTINCT (backend, bucket, object_key),
	CONSTRAINT fk_file_location_file_id_file_object FOREIGN KEY(file_id) REFERENCES storage.file_object (id) ON DELETE CASCADE
);

-- statement
CREATE INDEX ix_file_location_file_id ON storage.file_location (file_id);

-- statement
CREATE UNIQUE INDEX uq_file_location_primary ON storage.file_location (file_id) WHERE is_primary;

-- statement
CREATE TABLE story.story_container (
	node_id BIGINT NOT NULL,
	container_type VARCHAR(32) NOT NULL,
	title VARCHAR(512) NOT NULL,
	importance SMALLINT,
	metadata JSONB DEFAULT '{}'::jsonb NOT NULL,
	CONSTRAINT pk_story_container PRIMARY KEY (node_id),
	CONSTRAINT fk_story_container_node_id_node FOREIGN KEY(node_id) REFERENCES graph.node (id) ON DELETE CASCADE
);

-- statement
CREATE TABLE story.story_membership (
	parent_node_id BIGINT NOT NULL,
	child_node_id BIGINT NOT NULL,
	membership_type VARCHAR(32) NOT NULL,
	order_index INTEGER,
	metadata JSONB DEFAULT '{}'::jsonb NOT NULL,
	CONSTRAINT pk_story_membership PRIMARY KEY (parent_node_id, child_node_id, membership_type),
	CONSTRAINT uq_story_membership UNIQUE (parent_node_id, child_node_id, membership_type),
	CONSTRAINT fk_story_membership_parent_node_id_node FOREIGN KEY(parent_node_id) REFERENCES graph.node (id) ON DELETE CASCADE,
	CONSTRAINT fk_story_membership_child_node_id_node FOREIGN KEY(child_node_id) REFERENCES graph.node (id) ON DELETE CASCADE
);

-- statement
CREATE TABLE content.document (
	id BIGINT GENERATED ALWAYS AS IDENTITY,
	node_id BIGINT NOT NULL,
	locale_id SMALLINT,
	document_type VARCHAR(64) NOT NULL,
	revision INTEGER NOT NULL,
	title VARCHAR(512),
	plain_text TEXT NOT NULL,
	body_ast JSONB NOT NULL,
	source_hash BYTEA NOT NULL,
	processor_run_id BIGINT,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	metadata JSONB DEFAULT '{}'::jsonb NOT NULL,
	CONSTRAINT pk_document PRIMARY KEY (id),
	CONSTRAINT uq_content_document_revision UNIQUE (node_id, locale_id, document_type, revision),
	CONSTRAINT fk_document_node_id_node FOREIGN KEY(node_id) REFERENCES graph.node (id) ON DELETE CASCADE,
	CONSTRAINT fk_document_locale_id_locale FOREIGN KEY(locale_id) REFERENCES i18n.locale (id) ON DELETE SET NULL,
	CONSTRAINT fk_document_processor_run_id_processing_run FOREIGN KEY(processor_run_id) REFERENCES ops.processing_run (id) ON DELETE SET NULL
);

-- statement
CREATE INDEX ix_document_node_id ON content.document (node_id);

-- statement
CREATE TABLE core.voice_reference (
	node_id BIGINT NOT NULL,
	plot_audio_id VARCHAR(512),
	file_name VARCHAR(1024),
	media_asset_node_id BIGINT,
	metadata JSONB DEFAULT '{}'::jsonb NOT NULL,
	CONSTRAINT pk_voice_reference PRIMARY KEY (node_id),
	CONSTRAINT fk_voice_reference_node_id_node FOREIGN KEY(node_id) REFERENCES graph.node (id) ON DELETE CASCADE,
	CONSTRAINT fk_voice_reference_media_asset_node_id_media_asset FOREIGN KEY(media_asset_node_id) REFERENCES core.media_asset (node_id) ON DELETE SET NULL
);

-- statement
CREATE TABLE core.cutscene (
	node_id BIGINT NOT NULL,
	cg_name VARCHAR(256),
	cg_file VARCHAR(1024),
	media_asset_node_id BIGINT,
	metadata JSONB DEFAULT '{}'::jsonb NOT NULL,
	CONSTRAINT pk_cutscene PRIMARY KEY (node_id),
	CONSTRAINT fk_cutscene_node_id_node FOREIGN KEY(node_id) REFERENCES graph.node (id) ON DELETE CASCADE,
	CONSTRAINT fk_cutscene_media_asset_node_id_media_asset FOREIGN KEY(media_asset_node_id) REFERENCES core.media_asset (node_id) ON DELETE SET NULL
);

-- statement
CREATE TABLE graph.edge (
	id BIGINT GENERATED ALWAYS AS IDENTITY,
	from_node_id BIGINT NOT NULL,
	relation_type_id SMALLINT NOT NULL,
	to_node_id BIGINT NOT NULL,
	layer VARCHAR(24) NOT NULL,
	basis VARCHAR(32) NOT NULL,
	confidence DOUBLE PRECISION,
	created_release_id BIGINT,
	valid_from_release_id BIGINT,
	valid_to_release_id BIGINT,
	processor_run_id BIGINT,
	metadata JSONB DEFAULT '{}'::jsonb NOT NULL,
	CONSTRAINT pk_edge PRIMARY KEY (id),
	CONSTRAINT uq_edge_identity UNIQUE (from_node_id, relation_type_id, to_node_id, layer, basis),
	CONSTRAINT ck_edge_valid_layer CHECK (layer IN ('source','canonical','semantic','manual')),
	CONSTRAINT ck_edge_valid_basis CHECK (basis IN ('explicit_reference','exact_join','authored_order','source_array_adjacency','semantic_extraction','inference','manual')),
	CONSTRAINT fk_edge_from_node_id_node FOREIGN KEY(from_node_id) REFERENCES graph.node (id) ON DELETE CASCADE,
	CONSTRAINT fk_edge_relation_type_id_relation_type FOREIGN KEY(relation_type_id) REFERENCES ontology.relation_type (id) ON DELETE RESTRICT,
	CONSTRAINT fk_edge_to_node_id_node FOREIGN KEY(to_node_id) REFERENCES graph.node (id) ON DELETE CASCADE,
	CONSTRAINT fk_edge_created_release_id_game_release FOREIGN KEY(created_release_id) REFERENCES ops.game_release (id) ON DELETE SET NULL,
	CONSTRAINT fk_edge_valid_from_release_id_game_release FOREIGN KEY(valid_from_release_id) REFERENCES ops.game_release (id) ON DELETE SET NULL,
	CONSTRAINT fk_edge_valid_to_release_id_game_release FOREIGN KEY(valid_to_release_id) REFERENCES ops.game_release (id) ON DELETE SET NULL,
	CONSTRAINT fk_edge_processor_run_id_processing_run FOREIGN KEY(processor_run_id) REFERENCES ops.processing_run (id) ON DELETE SET NULL
);

-- statement
CREATE INDEX ix_edge_to_node_id ON graph.edge (to_node_id);

-- statement
CREATE INDEX ix_edge_from_relation ON graph.edge (from_node_id, relation_type_id);

-- statement
CREATE INDEX ix_edge_from_node_id ON graph.edge (from_node_id);

-- statement
CREATE INDEX ix_edge_to_relation ON graph.edge (to_node_id, relation_type_id);

-- statement
CREATE TABLE ontology.node_tag (
	node_id BIGINT NOT NULL,
	tag_id BIGINT NOT NULL,
	source VARCHAR(64) NOT NULL,
	confidence DOUBLE PRECISION,
	processor_run_id BIGINT,
	CONSTRAINT pk_node_tag PRIMARY KEY (node_id, tag_id),
	CONSTRAINT fk_node_tag_node_id_node FOREIGN KEY(node_id) REFERENCES graph.node (id) ON DELETE CASCADE,
	CONSTRAINT fk_node_tag_tag_id_tag FOREIGN KEY(tag_id) REFERENCES ontology.tag (id) ON DELETE CASCADE,
	CONSTRAINT fk_node_tag_processor_run_id_processing_run FOREIGN KEY(processor_run_id) REFERENCES ops.processing_run (id) ON DELETE SET NULL
);

-- statement
CREATE TABLE ontology.relation_candidate (
	id BIGINT GENERATED ALWAYS AS IDENTITY,
	proposed_key VARCHAR(128) NOT NULL,
	label VARCHAR(256) NOT NULL,
	description TEXT,
	sample_subject_node_id BIGINT,
	sample_object_node_id BIGINT,
	processor_run_id BIGINT,
	status VARCHAR(24) DEFAULT 'pending' NOT NULL,
	metadata JSONB DEFAULT '{}'::jsonb NOT NULL,
	CONSTRAINT pk_relation_candidate PRIMARY KEY (id),
	CONSTRAINT fk_relation_candidate_sample_subject_node_id_node FOREIGN KEY(sample_subject_node_id) REFERENCES graph.node (id) ON DELETE SET NULL,
	CONSTRAINT fk_relation_candidate_sample_object_node_id_node FOREIGN KEY(sample_object_node_id) REFERENCES graph.node (id) ON DELETE SET NULL,
	CONSTRAINT fk_relation_candidate_processor_run_id_processing_run FOREIGN KEY(processor_run_id) REFERENCES ops.processing_run (id) ON DELETE SET NULL
);

-- statement
CREATE TABLE ontology.tag_candidate (
	id BIGINT GENERATED ALWAYS AS IDENTITY,
	proposed_key VARCHAR(128) NOT NULL,
	label VARCHAR(256) NOT NULL,
	description TEXT,
	processor_run_id BIGINT,
	status VARCHAR(24) DEFAULT 'pending' NOT NULL,
	metadata JSONB DEFAULT '{}'::jsonb NOT NULL,
	CONSTRAINT pk_tag_candidate PRIMARY KEY (id),
	CONSTRAINT fk_tag_candidate_processor_run_id_processing_run FOREIGN KEY(processor_run_id) REFERENCES ops.processing_run (id) ON DELETE SET NULL
);

-- statement
CREATE TABLE raw.source_record (
	id BIGINT GENERATED ALWAYS AS IDENTITY,
	release_id BIGINT NOT NULL,
	source_file_id BIGINT NOT NULL,
	row_index BIGINT,
	source_key VARCHAR(1024),
	content_hash BYTEA NOT NULL,
	data JSONB NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_source_record PRIMARY KEY (id),
	CONSTRAINT uq_source_record_row UNIQUE (release_id, source_file_id, row_index),
	CONSTRAINT fk_source_record_release_id_game_release FOREIGN KEY(release_id) REFERENCES ops.game_release (id) ON DELETE RESTRICT,
	CONSTRAINT fk_source_record_source_file_id_source_file FOREIGN KEY(source_file_id) REFERENCES raw.source_file (id) ON DELETE RESTRICT
);

-- statement
CREATE INDEX ix_source_record_release_id ON raw.source_record (release_id);

-- statement
CREATE INDEX ix_source_record_source_file_id ON raw.source_record (source_file_id);

-- statement
CREATE INDEX ix_source_record_content_hash ON raw.source_record (content_hash);

-- statement
CREATE INDEX ix_source_record_source_key ON raw.source_record (source_key);

-- statement
CREATE TABLE storage.file_variant (
	parent_file_id BIGINT NOT NULL,
	child_file_id BIGINT NOT NULL,
	variant_type VARCHAR(64) NOT NULL,
	processor_run_id BIGINT,
	metadata JSONB DEFAULT '{}'::jsonb NOT NULL,
	CONSTRAINT pk_file_variant PRIMARY KEY (parent_file_id, child_file_id, variant_type),
	CONSTRAINT fk_file_variant_parent_file_id_file_object FOREIGN KEY(parent_file_id) REFERENCES storage.file_object (id) ON DELETE CASCADE,
	CONSTRAINT fk_file_variant_child_file_id_file_object FOREIGN KEY(child_file_id) REFERENCES storage.file_object (id) ON DELETE CASCADE,
	CONSTRAINT fk_file_variant_processor_run_id_processing_run FOREIGN KEY(processor_run_id) REFERENCES ops.processing_run (id) ON DELETE SET NULL
);

-- statement
CREATE TABLE story.scene (
	node_id BIGINT NOT NULL,
	quest_node_id BIGINT,
	title VARCHAR(512),
	authored_order INTEGER,
	source_basis VARCHAR(32) NOT NULL,
	metadata JSONB DEFAULT '{}'::jsonb NOT NULL,
	CONSTRAINT pk_scene PRIMARY KEY (node_id),
	CONSTRAINT fk_scene_node_id_node FOREIGN KEY(node_id) REFERENCES graph.node (id) ON DELETE CASCADE,
	CONSTRAINT fk_scene_quest_node_id_quest FOREIGN KEY(quest_node_id) REFERENCES core.quest (node_id) ON DELETE SET NULL
);

-- statement
CREATE TABLE story.event (
	node_id BIGINT NOT NULL,
	event_type_id SMALLINT,
	title VARCHAR(512) NOT NULL,
	importance SMALLINT,
	start_order INTEGER,
	end_order INTEGER,
	semantic_status VARCHAR(24) DEFAULT 'dirty' NOT NULL,
	processor_run_id BIGINT,
	metadata JSONB DEFAULT '{}'::jsonb NOT NULL,
	CONSTRAINT pk_event PRIMARY KEY (node_id),
	CONSTRAINT fk_event_node_id_node FOREIGN KEY(node_id) REFERENCES graph.node (id) ON DELETE CASCADE,
	CONSTRAINT fk_event_event_type_id_event_type FOREIGN KEY(event_type_id) REFERENCES ontology.event_type (id) ON DELETE SET NULL,
	CONSTRAINT fk_event_processor_run_id_processing_run FOREIGN KEY(processor_run_id) REFERENCES ops.processing_run (id) ON DELETE SET NULL
);

-- statement
CREATE INDEX ix_event_event_type_id ON story.event (event_type_id);

-- statement
CREATE TABLE story.claim (
	id BIGINT GENERATED ALWAYS AS IDENTITY,
	subject_node_id BIGINT,
	predicate_type_id SMALLINT,
	object_node_id BIGINT,
	object_text TEXT,
	claim_kind VARCHAR(24) NOT NULL,
	confidence DOUBLE PRECISION,
	status VARCHAR(24) DEFAULT 'pending' NOT NULL,
	processor_run_id BIGINT,
	metadata JSONB DEFAULT '{}'::jsonb NOT NULL,
	CONSTRAINT pk_claim PRIMARY KEY (id),
	CONSTRAINT fk_claim_subject_node_id_node FOREIGN KEY(subject_node_id) REFERENCES graph.node (id) ON DELETE SET NULL,
	CONSTRAINT fk_claim_predicate_type_id_relation_type FOREIGN KEY(predicate_type_id) REFERENCES ontology.relation_type (id) ON DELETE SET NULL,
	CONSTRAINT fk_claim_object_node_id_node FOREIGN KEY(object_node_id) REFERENCES graph.node (id) ON DELETE SET NULL,
	CONSTRAINT fk_claim_processor_run_id_processing_run FOREIGN KEY(processor_run_id) REFERENCES ops.processing_run (id) ON DELETE SET NULL
);

-- statement
CREATE INDEX ix_claim_predicate_type_id ON story.claim (predicate_type_id);

-- statement
CREATE INDEX ix_claim_object_node_id ON story.claim (object_node_id);

-- statement
CREATE INDEX ix_claim_subject_node_id ON story.claim (subject_node_id);

-- statement
CREATE TABLE content.document_head (
	id BIGINT GENERATED ALWAYS AS IDENTITY,
	node_id BIGINT NOT NULL,
	locale_id SMALLINT,
	document_type VARCHAR(64) NOT NULL,
	document_id BIGINT NOT NULL,
	CONSTRAINT pk_document_head PRIMARY KEY (id),
	CONSTRAINT uq_document_head_identity UNIQUE NULLS NOT DISTINCT (node_id, locale_id, document_type),
	CONSTRAINT fk_document_head_node_id_node FOREIGN KEY(node_id) REFERENCES graph.node (id) ON DELETE CASCADE,
	CONSTRAINT fk_document_head_locale_id_locale FOREIGN KEY(locale_id) REFERENCES i18n.locale (id) ON DELETE CASCADE,
	CONSTRAINT fk_document_head_document_id_document FOREIGN KEY(document_id) REFERENCES content.document (id) ON DELETE CASCADE
);

-- statement
CREATE TABLE content.document_reference (
	document_id BIGINT NOT NULL,
	ordinal INTEGER NOT NULL,
	target_node_id BIGINT,
	claim_id BIGINT,
	reference_type VARCHAR(32) NOT NULL,
	label VARCHAR(512),
	CONSTRAINT pk_document_reference PRIMARY KEY (document_id, ordinal),
	CONSTRAINT fk_document_reference_document_id_document FOREIGN KEY(document_id) REFERENCES content.document (id) ON DELETE CASCADE,
	CONSTRAINT fk_document_reference_target_node_id_node FOREIGN KEY(target_node_id) REFERENCES graph.node (id) ON DELETE CASCADE,
	CONSTRAINT fk_document_reference_claim_id_claim FOREIGN KEY(claim_id) REFERENCES story.claim (id) ON DELETE CASCADE
);

-- statement
CREATE INDEX ix_document_reference_target_node_id ON content.document_reference (target_node_id);

-- statement
CREATE TABLE core.npc (
	node_id BIGINT NOT NULL,
	game_npc_id BIGINT NOT NULL,
	source_record_id BIGINT,
	metadata JSONB DEFAULT '{}'::jsonb NOT NULL,
	CONSTRAINT pk_npc PRIMARY KEY (node_id),
	CONSTRAINT fk_npc_node_id_node FOREIGN KEY(node_id) REFERENCES graph.node (id) ON DELETE CASCADE,
	CONSTRAINT uq_npc_game_npc_id UNIQUE (game_npc_id),
	CONSTRAINT fk_npc_source_record_id_source_record FOREIGN KEY(source_record_id) REFERENCES raw.source_record (id) ON DELETE SET NULL
);

-- statement
CREATE TABLE core.speaker_entity_link (
	speaker_node_id BIGINT NOT NULL,
	entity_node_id BIGINT NOT NULL,
	resolution_type VARCHAR(32) NOT NULL,
	confidence DOUBLE PRECISION,
	source_record_id BIGINT,
	metadata JSONB DEFAULT '{}'::jsonb NOT NULL,
	CONSTRAINT pk_speaker_entity_link PRIMARY KEY (speaker_node_id, entity_node_id),
	CONSTRAINT ck_speaker_entity_link_valid_resolution_type CHECK (resolution_type IN ('explicit_crosswalk','unique_external_reference','manual_verified')),
	CONSTRAINT fk_speaker_entity_link_speaker_node_id_speaker FOREIGN KEY(speaker_node_id) REFERENCES core.speaker (node_id) ON DELETE CASCADE,
	CONSTRAINT fk_speaker_entity_link_entity_node_id_node FOREIGN KEY(entity_node_id) REFERENCES graph.node (id) ON DELETE CASCADE,
	CONSTRAINT fk_speaker_entity_link_source_record_id_source_record FOREIGN KEY(source_record_id) REFERENCES raw.source_record (id) ON DELETE SET NULL
);

-- statement
CREATE TABLE core.quest_node (
	node_id BIGINT NOT NULL,
	game_node_id VARCHAR(512) NOT NULL,
	game_quest_id BIGINT,
	node_type VARCHAR(64),
	source_record_id BIGINT,
	metadata JSONB DEFAULT '{}'::jsonb NOT NULL,
	CONSTRAINT pk_quest_node PRIMARY KEY (node_id),
	CONSTRAINT fk_quest_node_node_id_node FOREIGN KEY(node_id) REFERENCES graph.node (id) ON DELETE CASCADE,
	CONSTRAINT fk_quest_node_source_record_id_source_record FOREIGN KEY(source_record_id) REFERENCES raw.source_record (id) ON DELETE SET NULL
);

-- statement
CREATE INDEX ix_quest_node_game_quest_id ON core.quest_node (game_quest_id);

-- statement
CREATE INDEX ix_quest_node_game_node_id ON core.quest_node (game_node_id);

-- statement
CREATE TABLE core.quest_state (
	node_id BIGINT NOT NULL,
	state_key VARCHAR(512) NOT NULL,
	flow_list_name VARCHAR(256),
	flow_id BIGINT,
	state_id BIGINT,
	source_record_id BIGINT,
	metadata JSONB DEFAULT '{}'::jsonb NOT NULL,
	CONSTRAINT pk_quest_state PRIMARY KEY (node_id),
	CONSTRAINT fk_quest_state_node_id_node FOREIGN KEY(node_id) REFERENCES graph.node (id) ON DELETE CASCADE,
	CONSTRAINT fk_quest_state_source_record_id_source_record FOREIGN KEY(source_record_id) REFERENCES raw.source_record (id) ON DELETE SET NULL
);

-- statement
CREATE INDEX ix_quest_state_state_key ON core.quest_state (state_key);

-- statement
CREATE TABLE core.player_choice (
	node_id BIGINT NOT NULL,
	localization_key_id BIGINT,
	source_record_id BIGINT,
	metadata JSONB DEFAULT '{}'::jsonb NOT NULL,
	CONSTRAINT pk_player_choice PRIMARY KEY (node_id),
	CONSTRAINT fk_player_choice_node_id_node FOREIGN KEY(node_id) REFERENCES graph.node (id) ON DELETE CASCADE,
	CONSTRAINT fk_player_choice_localization_key_id_localization_key FOREIGN KEY(localization_key_id) REFERENCES i18n.localization_key (id) ON DELETE SET NULL,
	CONSTRAINT fk_player_choice_source_record_id_source_record FOREIGN KEY(source_record_id) REFERENCES raw.source_record (id) ON DELETE SET NULL
);

-- statement
CREATE TABLE core.narration (
	node_id BIGINT NOT NULL,
	localization_key_id BIGINT,
	narration_type VARCHAR(64),
	source_record_id BIGINT,
	metadata JSONB DEFAULT '{}'::jsonb NOT NULL,
	CONSTRAINT pk_narration PRIMARY KEY (node_id),
	CONSTRAINT fk_narration_node_id_node FOREIGN KEY(node_id) REFERENCES graph.node (id) ON DELETE CASCADE,
	CONSTRAINT fk_narration_localization_key_id_localization_key FOREIGN KEY(localization_key_id) REFERENCES i18n.localization_key (id) ON DELETE SET NULL,
	CONSTRAINT fk_narration_source_record_id_source_record FOREIGN KEY(source_record_id) REFERENCES raw.source_record (id) ON DELETE SET NULL
);

-- statement
CREATE TABLE core.conversation (
	node_id BIGINT NOT NULL,
	quest_node_id BIGINT,
	scene_node_id BIGINT,
	metadata JSONB DEFAULT '{}'::jsonb NOT NULL,
	CONSTRAINT pk_conversation PRIMARY KEY (node_id),
	CONSTRAINT fk_conversation_node_id_node FOREIGN KEY(node_id) REFERENCES graph.node (id) ON DELETE CASCADE,
	CONSTRAINT fk_conversation_quest_node_id_quest FOREIGN KEY(quest_node_id) REFERENCES core.quest (node_id) ON DELETE SET NULL,
	CONSTRAINT fk_conversation_scene_node_id_scene FOREIGN KEY(scene_node_id) REFERENCES story.scene (node_id) ON DELETE SET NULL
);

-- statement
CREATE TABLE core.audio_event (
	node_id BIGINT NOT NULL,
	event_path VARCHAR(1024),
	source_record_id BIGINT,
	metadata JSONB DEFAULT '{}'::jsonb NOT NULL,
	CONSTRAINT pk_audio_event PRIMARY KEY (node_id),
	CONSTRAINT fk_audio_event_node_id_node FOREIGN KEY(node_id) REFERENCES graph.node (id) ON DELETE CASCADE,
	CONSTRAINT fk_audio_event_source_record_id_source_record FOREIGN KEY(source_record_id) REFERENCES raw.source_record (id) ON DELETE SET NULL
);

-- statement
CREATE TABLE graph.node_revision (
	id BIGINT GENERATED ALWAYS AS IDENTITY,
	node_id BIGINT NOT NULL,
	release_id BIGINT,
	revision SMALLINT NOT NULL,
	content_hash BYTEA,
	source_record_id BIGINT,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	metadata JSONB DEFAULT '{}'::jsonb NOT NULL,
	CONSTRAINT pk_node_revision PRIMARY KEY (id),
	CONSTRAINT uq_node_revision UNIQUE (node_id, revision),
	CONSTRAINT fk_node_revision_node_id_node FOREIGN KEY(node_id) REFERENCES graph.node (id) ON DELETE CASCADE,
	CONSTRAINT fk_node_revision_release_id_game_release FOREIGN KEY(release_id) REFERENCES ops.game_release (id) ON DELETE SET NULL,
	CONSTRAINT fk_node_revision_source_record_id_source_record FOREIGN KEY(source_record_id) REFERENCES raw.source_record (id) ON DELETE SET NULL
);

-- statement
CREATE INDEX ix_node_revision_node_id ON graph.node_revision (node_id);

-- statement
CREATE TABLE graph.edge_evidence (
	id BIGINT GENERATED ALWAYS AS IDENTITY,
	edge_id BIGINT NOT NULL,
	evidence_node_id BIGINT,
	source_record_id BIGINT,
	source_file_path VARCHAR(2048),
	source_raw_path VARCHAR(4096),
	explanation TEXT,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_edge_evidence PRIMARY KEY (id),
	CONSTRAINT uq_edge_evidence_source UNIQUE NULLS NOT DISTINCT (edge_id, source_file_path, source_raw_path),
	CONSTRAINT fk_edge_evidence_edge_id_edge FOREIGN KEY(edge_id) REFERENCES graph.edge (id) ON DELETE CASCADE,
	CONSTRAINT fk_edge_evidence_evidence_node_id_node FOREIGN KEY(evidence_node_id) REFERENCES graph.node (id) ON DELETE SET NULL,
	CONSTRAINT fk_edge_evidence_source_record_id_source_record FOREIGN KEY(source_record_id) REFERENCES raw.source_record (id) ON DELETE SET NULL
);

-- statement
CREATE INDEX ix_edge_evidence_edge_id ON graph.edge_evidence (edge_id);

-- statement
CREATE INDEX ix_edge_evidence_source_record_id ON graph.edge_evidence (source_record_id);

-- statement
CREATE TABLE i18n.localization_value (
	release_id BIGINT NOT NULL,
	key_id BIGINT NOT NULL,
	locale_id SMALLINT NOT NULL,
	content TEXT,
	status VARCHAR(32) NOT NULL,
	redirect_key_id BIGINT,
	source_record_id BIGINT,
	content_hash BYTEA,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_localization_value PRIMARY KEY (release_id, key_id, locale_id),
	CONSTRAINT uq_localization_release_key_locale UNIQUE (release_id, key_id, locale_id),
	CONSTRAINT fk_localization_value_release_id_game_release FOREIGN KEY(release_id) REFERENCES ops.game_release (id) ON DELETE RESTRICT,
	CONSTRAINT fk_localization_value_key_id_localization_key FOREIGN KEY(key_id) REFERENCES i18n.localization_key (id) ON DELETE RESTRICT,
	CONSTRAINT fk_localization_value_locale_id_locale FOREIGN KEY(locale_id) REFERENCES i18n.locale (id) ON DELETE RESTRICT,
	CONSTRAINT fk_localization_value_redirect_key_id_localization_key FOREIGN KEY(redirect_key_id) REFERENCES i18n.localization_key (id) ON DELETE SET NULL,
	CONSTRAINT fk_localization_value_source_record_id_source_record FOREIGN KEY(source_record_id) REFERENCES raw.source_record (id) ON DELETE SET NULL
);

-- statement
CREATE TABLE storage.file_reference (
	id BIGINT GENERATED ALWAYS AS IDENTITY,
	release_id BIGINT,
	owner_node_id BIGINT,
	file_id BIGINT,
	reference_type VARCHAR(64) NOT NULL,
	source_name VARCHAR(1024),
	source_path VARCHAR(2048),
	source_record_id BIGINT,
	metadata JSONB DEFAULT '{}'::jsonb NOT NULL,
	CONSTRAINT pk_file_reference PRIMARY KEY (id),
	CONSTRAINT fk_file_reference_release_id_game_release FOREIGN KEY(release_id) REFERENCES ops.game_release (id) ON DELETE SET NULL,
	CONSTRAINT fk_file_reference_owner_node_id_node FOREIGN KEY(owner_node_id) REFERENCES graph.node (id) ON DELETE SET NULL,
	CONSTRAINT fk_file_reference_file_id_file_object FOREIGN KEY(file_id) REFERENCES storage.file_object (id) ON DELETE SET NULL,
	CONSTRAINT fk_file_reference_source_record_id_source_record FOREIGN KEY(source_record_id) REFERENCES raw.source_record (id) ON DELETE SET NULL
);

-- statement
CREATE INDEX ix_file_reference_file_id ON storage.file_reference (file_id);

-- statement
CREATE INDEX ix_file_reference_owner_node_id ON storage.file_reference (owner_node_id);

-- statement
CREATE INDEX ix_file_reference_release_id ON storage.file_reference (release_id);

-- statement
CREATE TABLE story.claim_evidence (
	claim_id BIGINT NOT NULL,
	evidence_node_id BIGINT NOT NULL,
	relevance DOUBLE PRECISION,
	metadata JSONB DEFAULT '{}'::jsonb NOT NULL,
	CONSTRAINT pk_claim_evidence PRIMARY KEY (claim_id, evidence_node_id),
	CONSTRAINT fk_claim_evidence_claim_id_claim FOREIGN KEY(claim_id) REFERENCES story.claim (id) ON DELETE CASCADE,
	CONSTRAINT fk_claim_evidence_evidence_node_id_node FOREIGN KEY(evidence_node_id) REFERENCES graph.node (id) ON DELETE CASCADE
);

-- statement
CREATE TABLE core.quest_action (
	node_id BIGINT NOT NULL,
	quest_state_node_id BIGINT NOT NULL,
	action_id VARCHAR(256),
	action_guid VARCHAR(256),
	action_name VARCHAR(128) NOT NULL,
	action_index INTEGER NOT NULL,
	params JSONB NOT NULL,
	source_record_id BIGINT,
	metadata JSONB DEFAULT '{}'::jsonb NOT NULL,
	CONSTRAINT pk_quest_action PRIMARY KEY (node_id),
	CONSTRAINT uq_quest_action_state_index UNIQUE (quest_state_node_id, action_index),
	CONSTRAINT fk_quest_action_node_id_node FOREIGN KEY(node_id) REFERENCES graph.node (id) ON DELETE CASCADE,
	CONSTRAINT fk_quest_action_quest_state_node_id_quest_state FOREIGN KEY(quest_state_node_id) REFERENCES core.quest_state (node_id) ON DELETE CASCADE,
	CONSTRAINT fk_quest_action_source_record_id_source_record FOREIGN KEY(source_record_id) REFERENCES raw.source_record (id) ON DELETE SET NULL
);

-- statement
CREATE INDEX ix_quest_action_quest_state_node_id ON core.quest_action (quest_state_node_id);

-- statement
CREATE TABLE core.phone_message (
	node_id BIGINT NOT NULL,
	conversation_node_id BIGINT,
	speaker_node_id BIGINT,
	localization_key_id BIGINT,
	source_record_id BIGINT,
	metadata JSONB DEFAULT '{}'::jsonb NOT NULL,
	CONSTRAINT pk_phone_message PRIMARY KEY (node_id),
	CONSTRAINT fk_phone_message_node_id_node FOREIGN KEY(node_id) REFERENCES graph.node (id) ON DELETE CASCADE,
	CONSTRAINT fk_phone_message_conversation_node_id_conversation FOREIGN KEY(conversation_node_id) REFERENCES core.conversation (node_id) ON DELETE SET NULL,
	CONSTRAINT fk_phone_message_speaker_node_id_speaker FOREIGN KEY(speaker_node_id) REFERENCES core.speaker (node_id) ON DELETE SET NULL,
	CONSTRAINT fk_phone_message_localization_key_id_localization_key FOREIGN KEY(localization_key_id) REFERENCES i18n.localization_key (id) ON DELETE SET NULL,
	CONSTRAINT fk_phone_message_source_record_id_source_record FOREIGN KEY(source_record_id) REFERENCES raw.source_record (id) ON DELETE SET NULL
);

-- statement
CREATE TABLE core.conversation_member (
	conversation_node_id BIGINT NOT NULL,
	node_id BIGINT NOT NULL,
	order_index INTEGER,
	ordering_basis VARCHAR(32) NOT NULL,
	metadata JSONB DEFAULT '{}'::jsonb NOT NULL,
	CONSTRAINT pk_conversation_member PRIMARY KEY (conversation_node_id, node_id),
	CONSTRAINT fk_conversation_member_conversation_node_id_conversation FOREIGN KEY(conversation_node_id) REFERENCES core.conversation (node_id) ON DELETE CASCADE,
	CONSTRAINT fk_conversation_member_node_id_node FOREIGN KEY(node_id) REFERENCES graph.node (id) ON DELETE CASCADE
);

-- statement
CREATE TABLE core.dialogue_line (
	node_id BIGINT NOT NULL,
	action_node_id BIGINT,
	speaker_node_id BIGINT,
	localization_key_id BIGINT,
	inline_text TEXT,
	game_talk_item_id VARCHAR(512),
	game_text_id VARCHAR(512),
	source_type VARCHAR(64),
	source_index INTEGER,
	source_record_id BIGINT,
	metadata JSONB DEFAULT '{}'::jsonb NOT NULL,
	CONSTRAINT pk_dialogue_line PRIMARY KEY (node_id),
	CONSTRAINT fk_dialogue_line_node_id_node FOREIGN KEY(node_id) REFERENCES graph.node (id) ON DELETE CASCADE,
	CONSTRAINT fk_dialogue_line_action_node_id_quest_action FOREIGN KEY(action_node_id) REFERENCES core.quest_action (node_id) ON DELETE SET NULL,
	CONSTRAINT fk_dialogue_line_speaker_node_id_speaker FOREIGN KEY(speaker_node_id) REFERENCES core.speaker (node_id) ON DELETE SET NULL,
	CONSTRAINT fk_dialogue_line_localization_key_id_localization_key FOREIGN KEY(localization_key_id) REFERENCES i18n.localization_key (id) ON DELETE SET NULL,
	CONSTRAINT fk_dialogue_line_source_record_id_source_record FOREIGN KEY(source_record_id) REFERENCES raw.source_record (id) ON DELETE SET NULL
);

-- statement
CREATE INDEX ix_dialogue_localization_key ON core.dialogue_line (localization_key_id);

-- statement
CREATE INDEX ix_dialogue_speaker ON core.dialogue_line (speaker_node_id);

-- statement
CREATE TABLE search.dialogue_chunk (
	id BIGINT GENERATED ALWAYS AS IDENTITY,
	scene_node_id BIGINT,
	conversation_node_id BIGINT,
	start_line_node_id BIGINT,
	end_line_node_id BIGINT,
	ordinal INTEGER NOT NULL,
	locale_id SMALLINT NOT NULL,
	text TEXT NOT NULL,
	content_hash BYTEA NOT NULL,
	CONSTRAINT pk_dialogue_chunk PRIMARY KEY (id),
	CONSTRAINT uq_dialogue_chunk UNIQUE NULLS NOT DISTINCT (scene_node_id, conversation_node_id, ordinal, locale_id),
	CONSTRAINT fk_dialogue_chunk_scene_node_id_scene FOREIGN KEY(scene_node_id) REFERENCES story.scene (node_id) ON DELETE CASCADE,
	CONSTRAINT fk_dialogue_chunk_conversation_node_id_conversation FOREIGN KEY(conversation_node_id) REFERENCES core.conversation (node_id) ON DELETE CASCADE,
	CONSTRAINT fk_dialogue_chunk_start_line_node_id_dialogue_line FOREIGN KEY(start_line_node_id) REFERENCES core.dialogue_line (node_id) ON DELETE SET NULL,
	CONSTRAINT fk_dialogue_chunk_end_line_node_id_dialogue_line FOREIGN KEY(end_line_node_id) REFERENCES core.dialogue_line (node_id) ON DELETE SET NULL,
	CONSTRAINT fk_dialogue_chunk_locale_id_locale FOREIGN KEY(locale_id) REFERENCES i18n.locale (id) ON DELETE RESTRICT
);

-- statement
CREATE TABLE search.dialogue_chunk_embedding (
	id BIGINT GENERATED ALWAYS AS IDENTITY,
	chunk_id BIGINT NOT NULL,
	model_id BIGINT NOT NULL,
	content_hash BYTEA NOT NULL,
	embedding VECTOR NOT NULL,
	CONSTRAINT pk_dialogue_chunk_embedding PRIMARY KEY (id),
	CONSTRAINT uq_dialogue_chunk_embedding UNIQUE (chunk_id, model_id),
	CONSTRAINT fk_dialogue_chunk_embedding_chunk_id_dialogue_chunk FOREIGN KEY(chunk_id) REFERENCES search.dialogue_chunk (id) ON DELETE CASCADE,
	CONSTRAINT fk_dialogue_chunk_embedding_model_id_embedding_model FOREIGN KEY(model_id) REFERENCES search.embedding_model (id) ON DELETE RESTRICT
);

-- statement
CREATE FUNCTION search.refresh_document_vector() RETURNS trigger AS $$
    BEGIN
      NEW.search_vector :=
        setweight(to_tsvector('simple', coalesce(NEW.title, '')), 'A') ||
        setweight(to_tsvector('simple', coalesce(NEW.body, '')), 'B');
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;

-- statement
CREATE TRIGGER trg_search_document_vector
    BEFORE INSERT OR UPDATE OF title, body ON search.document
    FOR EACH ROW EXECUTE FUNCTION search.refresh_document_vector();
