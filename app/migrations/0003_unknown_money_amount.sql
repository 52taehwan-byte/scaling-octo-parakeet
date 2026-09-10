-- Money records may describe a confirmed business fact whose exact amount is
-- still unknown.  NULL means unknown; zero remains a real, confirmed zero.
PRAGMA defer_foreign_keys = ON;

CREATE TABLE money_items_new (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    site_id TEXT NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    lineage_key TEXT NOT NULL CHECK (length(trim(lineage_key)) > 0),
    title TEXT NOT NULL CHECK (length(trim(title)) > 0),
    description TEXT,
    amount_krw INTEGER CHECK (amount_krw IS NULL OR amount_krw >= 0),
    purpose TEXT NOT NULL CHECK (purpose IN (
        'base_quote','addon_quote','discount','commission','labor_cost','equipment_cost',
        'waste_cost','material_cost','transport_cost','outsource_cost','scrap_income',
        'deposit','receivable','settlement','refund','tax','other'
    )),
    progression TEXT NOT NULL CHECK (progression IN (
        'candidate','offered','agreed','claimed','confirmed','settled','void'
    )),
    actualness TEXT NOT NULL CHECK (actualness IN ('estimated','actual')),
    direction TEXT NOT NULL CHECK (direction IN ('inflow','outflow','neutral')),
    epistemic_type TEXT NOT NULL CHECK (epistemic_type IN (
        'fact','claim','inference','decision','recommendation'
    )),
    review_status TEXT NOT NULL CHECK (review_status IN (
        'pending','approved','corrected','held','rejected'
    )),
    counterparty_person_id TEXT REFERENCES persons(id) ON DELETE SET NULL,
    occurred_at TEXT,
    due_at TEXT,
    settled_at TEXT,
    supersedes_money_item_id TEXT UNIQUE REFERENCES money_items_new(id) ON DELETE RESTRICT,
    quantity REAL CHECK (quantity IS NULL OR quantity > 0),
    unit TEXT,
    unit_price_krw INTEGER CHECK (unit_price_krw IS NULL OR unit_price_krw >= 0),
    tax_included INTEGER CHECK (tax_included IS NULL OR tax_included IN (0,1)),
    notes TEXT,
    evidence_ref TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    deleted_at TEXT,
    legacy_status TEXT NOT NULL DEFAULT 'native' CHECK (legacy_status IN (
        'native','migrated_confirmed','migrated_review_required','superseded',
        'reference_only','excluded'
    )),
    legacy_ref TEXT
);

INSERT INTO money_items_new
SELECT * FROM money_items;

DROP TABLE money_items;
ALTER TABLE money_items_new RENAME TO money_items;

CREATE UNIQUE INDEX money_items_one_root_per_lineage
ON money_items(site_id, lineage_key)
WHERE supersedes_money_item_id IS NULL AND deleted_at IS NULL;

CREATE INDEX money_items_site_idx
ON money_items(site_id, created_at);

CREATE INDEX money_items_lineage_idx
ON money_items(site_id, lineage_key, created_at);
