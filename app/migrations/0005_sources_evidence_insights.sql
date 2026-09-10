CREATE TABLE sources (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    site_id TEXT REFERENCES sites(id) ON DELETE SET NULL,
    source_type TEXT NOT NULL CHECK (source_type IN (
        'audio','transcript','message_export','photo','document','manual_input','migration_preview','other'
    )),
    original_name TEXT,
    mime_type TEXT,
    byte_size INTEGER CHECK (byte_size IS NULL OR byte_size >= 0),
    content_hash_sha256 TEXT NOT NULL CHECK (length(content_hash_sha256) = 64),
    storage_uri TEXT NOT NULL CHECK (length(trim(storage_uri)) > 0),
    captured_at TEXT,
    imported_at TEXT NOT NULL,
    source_timezone TEXT,
    sender_person_id TEXT REFERENCES persons(id) ON DELETE SET NULL,
    receiver_person_id TEXT REFERENCES persons(id) ON DELETE SET NULL,
    parent_source_id TEXT REFERENCES sources(id) ON DELETE RESTRICT,
    processing_status TEXT NOT NULL DEFAULT 'unprocessed' CHECK (
        processing_status IN ('unprocessed','processing','processed','failed','excluded')
    ),
    privacy_level TEXT NOT NULL DEFAULT 'sensitive' CHECK (
        privacy_level IN ('private','sensitive','restricted')
    ),
    retention_policy TEXT NOT NULL DEFAULT 'keep_until_user_deletes',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    deleted_at TEXT,
    legacy_status TEXT NOT NULL DEFAULT 'native' CHECK (legacy_status IN (
        'native','migrated_confirmed','migrated_review_required','superseded','reference_only','excluded'
    )),
    legacy_ref TEXT
);

CREATE UNIQUE INDEX sources_workspace_hash_active
ON sources(workspace_id, content_hash_sha256)
WHERE deleted_at IS NULL;
CREATE INDEX sources_site_imported ON sources(site_id, imported_at DESC);

CREATE TABLE evidence (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    evidence_type TEXT NOT NULL CHECK (evidence_type IN (
        'whole_source','text_range','audio_range','image_region','document_page','other'
    )),
    text_start INTEGER CHECK (text_start IS NULL OR text_start >= 0),
    text_end INTEGER CHECK (text_end IS NULL OR text_end >= 0),
    audio_start_ms INTEGER CHECK (audio_start_ms IS NULL OR audio_start_ms >= 0),
    audio_end_ms INTEGER CHECK (audio_end_ms IS NULL OR audio_end_ms >= 0),
    page_number INTEGER CHECK (page_number IS NULL OR page_number >= 1),
    bounding_box_json TEXT,
    excerpt TEXT,
    evidence_hash_sha256 TEXT CHECK (evidence_hash_sha256 IS NULL OR length(evidence_hash_sha256) = 64),
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    deleted_at TEXT,
    CHECK (text_start IS NULL OR text_end IS NULL OR text_end >= text_start),
    CHECK (audio_start_ms IS NULL OR audio_end_ms IS NULL OR audio_end_ms >= audio_start_ms)
);

CREATE INDEX evidence_source ON evidence(source_id, created_at);

CREATE TABLE insights (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    site_id TEXT REFERENCES sites(id) ON DELETE CASCADE,
    insight_type TEXT NOT NULL CHECK (insight_type IN (
        'profitability','schedule','customer','safety','process','learning','question','next_action','other'
    )),
    title TEXT NOT NULL CHECK (length(trim(title)) > 0),
    body TEXT NOT NULL CHECK (length(trim(body)) > 0),
    evidence_id TEXT REFERENCES evidence(id) ON DELETE SET NULL,
    epistemic_type TEXT NOT NULL CHECK (epistemic_type IN ('inference','recommendation')),
    review_status TEXT NOT NULL CHECK (review_status IN ('pending','approved','held','rejected')),
    confidence REAL CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    valid_until TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    deleted_at TEXT
);

CREATE INDEX insights_workspace_review ON insights(workspace_id, review_status, created_at DESC);
CREATE INDEX insights_site ON insights(site_id, created_at DESC);
