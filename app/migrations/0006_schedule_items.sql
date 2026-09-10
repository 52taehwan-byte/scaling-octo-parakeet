CREATE TABLE schedule_items (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    site_id TEXT REFERENCES sites(id) ON DELETE SET NULL,
    schedule_type TEXT NOT NULL CHECK (schedule_type IN ('work','estimate_visit')),
    title TEXT NOT NULL CHECK (length(trim(title)) > 0),
    start_at TEXT NOT NULL CHECK (length(trim(start_at)) > 0),
    end_at TEXT,
    summary TEXT NOT NULL CHECK (length(trim(summary)) > 0),
    customer_name TEXT,
    customer_contact TEXT,
    address_text TEXT,
    participants_text TEXT,
    notes TEXT,
    business_status TEXT NOT NULL DEFAULT 'scheduled'
        CHECK (business_status IN ('scheduled','completed','cancelled')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    deleted_at TEXT,
    CHECK (end_at IS NULL OR end_at >= start_at),
    CHECK (schedule_type != 'work' OR site_id IS NOT NULL)
);

CREATE INDEX schedule_items_workspace_start
ON schedule_items(workspace_id, start_at);

CREATE INDEX schedule_items_workspace_type_start
ON schedule_items(workspace_id, schedule_type, start_at);
