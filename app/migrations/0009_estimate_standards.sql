CREATE TABLE estimate_standards (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    category TEXT NOT NULL CHECK (category IN ('process','labor','equipment','waste','margin','restoration','condition','other')),
    title TEXT NOT NULL,
    rule_text TEXT NOT NULL,
    rationale TEXT,
    source_note TEXT,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1,
    deleted_at TEXT
);

CREATE INDEX estimate_standards_workspace
ON estimate_standards(workspace_id, is_active, category, sort_order, created_at);
