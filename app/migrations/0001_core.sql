CREATE TABLE workspaces (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL CHECK (length(trim(name)) > 0),
    owner_person_id TEXT REFERENCES persons(id) ON DELETE SET NULL,
    industry_pack_id TEXT NOT NULL DEFAULT 'demolition',
    industry_pack_version TEXT NOT NULL DEFAULT '0.1.0',
    timezone TEXT NOT NULL DEFAULT 'Asia/Seoul',
    currency TEXT NOT NULL DEFAULT 'KRW',
    settings_json TEXT NOT NULL DEFAULT '{}',
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

CREATE TABLE persons (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    display_name TEXT NOT NULL CHECK (length(trim(display_name)) > 0),
    person_kind TEXT NOT NULL DEFAULT 'individual' CHECK (person_kind IN (
        'individual','organization_contact','unknown'
    )),
    organization_name TEXT,
    phone_encrypted TEXT,
    messenger_handle_encrypted TEXT,
    notes TEXT,
    privacy_level TEXT NOT NULL DEFAULT 'private' CHECK (privacy_level IN (
        'private','sensitive','restricted'
    )),
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

CREATE TABLE sites (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    name TEXT NOT NULL CHECK (length(trim(name)) > 0),
    address_text TEXT,
    address_privacy_level TEXT NOT NULL DEFAULT 'private' CHECK (
        address_privacy_level IN ('private','sensitive','restricted')
    ),
    business_status TEXT NOT NULL DEFAULT 'lead' CHECK (business_status IN (
        'lead','estimating','scheduled','in_progress','paused','completed','settled','cancelled'
    )),
    cost_completeness TEXT NOT NULL DEFAULT 'unknown' CHECK (
        cost_completeness IN ('unknown','partial','complete')
    ),
    lead_received_at TEXT,
    planned_start_at TEXT,
    planned_end_at TEXT,
    actual_start_at TEXT,
    actual_end_at TEXT,
    client_person_id TEXT REFERENCES persons(id) ON DELETE SET NULL,
    scope_summary TEXT,
    notes TEXT,
    industry_data_json TEXT NOT NULL DEFAULT '{}',
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

CREATE TABLE site_roles (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    site_id TEXT NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    person_id TEXT NOT NULL REFERENCES persons(id) ON DELETE RESTRICT,
    role_type TEXT NOT NULL CHECK (role_type IN (
        'client','buyer','site_manager','owner','employee','skilled_worker',
        'helper','subcontractor','equipment_operator','waste_vendor','other'
    )),
    role_label TEXT,
    valid_from TEXT,
    valid_to TEXT,
    is_primary_contact INTEGER NOT NULL DEFAULT 0 CHECK (is_primary_contact IN (0,1)),
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

CREATE TABLE events (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    site_id TEXT NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL CHECK (length(trim(event_type)) > 0),
    title TEXT NOT NULL CHECK (length(trim(title)) > 0),
    description TEXT,
    occurred_at TEXT,
    occurred_end_at TEXT,
    time_precision TEXT NOT NULL DEFAULT 'unknown' CHECK (
        time_precision IN ('exact','date','approximate','range','unknown')
    ),
    raw_time_text TEXT,
    business_status TEXT NOT NULL DEFAULT 'occurred' CHECK (
        business_status IN ('planned','occurred','cancelled')
    ),
    epistemic_type TEXT NOT NULL CHECK (epistemic_type IN (
        'fact','claim','inference','decision','recommendation'
    )),
    review_status TEXT NOT NULL CHECK (review_status IN (
        'pending','approved','corrected','held','rejected'
    )),
    actor_person_id TEXT REFERENCES persons(id) ON DELETE SET NULL,
    evidence_ref TEXT,
    supersedes_event_id TEXT UNIQUE REFERENCES events(id) ON DELETE RESTRICT,
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

CREATE TABLE tasks (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    site_id TEXT NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    title TEXT NOT NULL CHECK (length(trim(title)) > 0),
    description TEXT,
    phase TEXT NOT NULL DEFAULT 'other' CHECK (phase IN (
        'pre_estimate','contract','preparation','demolition','waste',
        'restoration','settlement','aftercare','other'
    )),
    assignee_person_id TEXT REFERENCES persons(id) ON DELETE SET NULL,
    requested_by_person_id TEXT REFERENCES persons(id) ON DELETE SET NULL,
    planned_start_at TEXT,
    due_at TEXT,
    completed_at TEXT,
    priority TEXT NOT NULL DEFAULT 'normal' CHECK (priority IN ('low','normal','high','urgent')),
    business_status TEXT NOT NULL DEFAULT 'open' CHECK (business_status IN (
        'open','in_progress','blocked','done','cancelled'
    )),
    blocked_reason TEXT,
    epistemic_type TEXT NOT NULL CHECK (epistemic_type IN (
        'fact','claim','inference','decision','recommendation'
    )),
    review_status TEXT NOT NULL CHECK (review_status IN (
        'pending','approved','corrected','held','rejected'
    )),
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

CREATE TABLE money_items (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    site_id TEXT NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    lineage_key TEXT NOT NULL CHECK (length(trim(lineage_key)) > 0),
    title TEXT NOT NULL CHECK (length(trim(title)) > 0),
    description TEXT,
    amount_krw INTEGER NOT NULL CHECK (amount_krw >= 0),
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
    supersedes_money_item_id TEXT UNIQUE REFERENCES money_items(id) ON DELETE RESTRICT,
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

CREATE UNIQUE INDEX money_items_one_root_per_lineage
ON money_items(site_id, lineage_key)
WHERE supersedes_money_item_id IS NULL AND deleted_at IS NULL;

CREATE TABLE risks (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    site_id TEXT NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    category TEXT NOT NULL CHECK (category IN (
        'safety','scope','customer','schedule','cost','legal','quality','other'
    )),
    title TEXT NOT NULL CHECK (length(trim(title)) > 0),
    description TEXT,
    severity TEXT NOT NULL DEFAULT 'medium' CHECK (severity IN ('low','medium','high','critical')),
    likelihood TEXT NOT NULL DEFAULT 'possible' CHECK (likelihood IN ('unlikely','possible','likely')),
    business_status TEXT NOT NULL DEFAULT 'open' CHECK (business_status IN (
        'open','monitoring','mitigated','occurred','closed'
    )),
    estimated_extra_cost_money_item_id TEXT REFERENCES money_items(id) ON DELETE SET NULL,
    estimated_delay_minutes INTEGER CHECK (estimated_delay_minutes IS NULL OR estimated_delay_minutes >= 0),
    response_plan TEXT,
    owner_person_id TEXT REFERENCES persons(id) ON DELETE SET NULL,
    due_at TEXT,
    resolved_at TEXT,
    resolution_note TEXT,
    resolution_evidence_ref TEXT,
    epistemic_type TEXT NOT NULL CHECK (epistemic_type IN (
        'fact','claim','inference','decision','recommendation'
    )),
    review_status TEXT NOT NULL CHECK (review_status IN (
        'pending','approved','corrected','held','rejected'
    )),
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

CREATE TABLE reviews (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    target_type TEXT NOT NULL CHECK (target_type IN ('event','task','money_item','risk')),
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

CREATE TABLE audit_events (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    action_type TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    actor_type TEXT NOT NULL CHECK (actor_type IN ('user','ai','system','migration')),
    actor_id TEXT,
    before_json TEXT,
    after_json TEXT,
    reason TEXT,
    occurred_at TEXT NOT NULL,
    source_request_id TEXT,
    app_version TEXT
);
