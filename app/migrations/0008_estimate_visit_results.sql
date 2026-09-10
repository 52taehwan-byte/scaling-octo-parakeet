CREATE TABLE estimate_visit_results (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    schedule_id TEXT NOT NULL UNIQUE REFERENCES schedule_items(id) ON DELETE CASCADE,
    customer_requests TEXT NOT NULL,
    work_plan TEXT,
    labor_cost_krw INTEGER CHECK (labor_cost_krw IS NULL OR labor_cost_krw >= 0),
    equipment_plan TEXT,
    equipment_cost_krw INTEGER CHECK (equipment_cost_krw IS NULL OR equipment_cost_krw >= 0),
    waste_plan TEXT,
    waste_cost_krw INTEGER CHECK (waste_cost_krw IS NULL OR waste_cost_krw >= 0),
    restoration_plan TEXT,
    restoration_cost_krw INTEGER CHECK (restoration_cost_krw IS NULL OR restoration_cost_krw >= 0),
    total_quote_krw INTEGER CHECK (total_quote_krw IS NULL OR total_quote_krw >= 0),
    conditions_text TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1)
);

CREATE INDEX estimate_visit_results_workspace
ON estimate_visit_results(workspace_id, updated_at);
