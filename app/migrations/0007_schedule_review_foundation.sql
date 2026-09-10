PRAGMA foreign_keys = OFF;

CREATE TABLE schedule_items_new (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    site_id TEXT REFERENCES sites(id) ON DELETE SET NULL,
    schedule_type TEXT NOT NULL CHECK (schedule_type IN ('work','estimate_visit')),
    title TEXT NOT NULL CHECK (length(trim(title)) > 0),
    start_at TEXT NOT NULL CHECK (length(trim(start_at)) > 0),
    end_at TEXT,
    time_precision TEXT NOT NULL DEFAULT 'exact' CHECK (time_precision IN ('exact','date','range','approximate')),
    summary TEXT NOT NULL CHECK (length(trim(summary)) > 0),
    customer_name TEXT,
    customer_contact TEXT,
    address_text TEXT,
    participants_text TEXT,
    notes TEXT,
    business_status TEXT NOT NULL DEFAULT 'scheduled'
        CHECK (business_status IN ('scheduled','completed','cancelled')),
    epistemic_type TEXT NOT NULL DEFAULT 'decision'
        CHECK (epistemic_type IN ('fact','claim','inference','decision','recommendation')),
    review_status TEXT NOT NULL DEFAULT 'approved'
        CHECK (review_status IN ('pending','approved','held','rejected')),
    evidence_ref TEXT,
    confidence REAL CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    source_comparison TEXT CHECK (source_comparison IS NULL OR source_comparison IN (
        'matched','kakao_only','markdown_only','conflict','later_confirmation','manual'
    )),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    deleted_at TEXT,
    legacy_status TEXT NOT NULL DEFAULT 'native' CHECK (legacy_status IN (
        'native','migrated_confirmed','migrated_review_required','superseded','reference_only','excluded'
    )),
    CHECK (end_at IS NULL OR end_at >= start_at),
    CHECK (schedule_type != 'work' OR site_id IS NOT NULL)
);

INSERT INTO schedule_items_new (
    id,workspace_id,site_id,schedule_type,title,start_at,end_at,time_precision,summary,
    customer_name,customer_contact,address_text,participants_text,notes,business_status,
    epistemic_type,review_status,evidence_ref,confidence,source_comparison,
    created_at,updated_at,created_by,revision,deleted_at,legacy_status
)
SELECT
    id,workspace_id,site_id,schedule_type,title,start_at,end_at,'exact',summary,
    customer_name,customer_contact,address_text,participants_text,notes,business_status,
    'decision','approved',NULL,NULL,'manual',
    created_at,updated_at,created_by,revision,deleted_at,'native'
FROM schedule_items;

DROP TABLE schedule_items;
ALTER TABLE schedule_items_new RENAME TO schedule_items;

CREATE INDEX schedule_items_workspace_start
ON schedule_items(workspace_id, start_at);

CREATE INDEX schedule_items_workspace_type_start
ON schedule_items(workspace_id, schedule_type, start_at);

CREATE INDEX schedule_items_workspace_review
ON schedule_items(workspace_id, review_status, created_at);

CREATE TABLE reviews_new (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    target_type TEXT NOT NULL CHECK (target_type IN ('event','task','money_item','risk','schedule_item')),
    target_id TEXT NOT NULL,
    target_revision INTEGER NOT NULL CHECK (target_revision >= 1),
    action TEXT NOT NULL CHECK (action IN ('approve','correct','hold','reject','reopen')),
    reason TEXT,
    before_json TEXT NOT NULL,
    after_json TEXT NOT NULL,
    reviewer_person_id TEXT REFERENCES persons(id) ON DELETE SET NULL,
    reviewed_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

INSERT INTO reviews_new
SELECT id,workspace_id,target_type,target_id,target_revision,action,reason,
       before_json,after_json,reviewer_person_id,reviewed_at,created_at
FROM reviews;

DROP TABLE reviews;
ALTER TABLE reviews_new RENAME TO reviews;

PRAGMA foreign_keys = ON;
