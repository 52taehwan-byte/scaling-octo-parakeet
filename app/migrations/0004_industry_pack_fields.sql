-- Keep universal business states separate from demolition-pack detail.
ALTER TABLE tasks ADD COLUMN industry_phase TEXT;
ALTER TABLE risks ADD COLUMN industry_category TEXT;

-- Manually confirmed role links can retain their source. AI classification of
-- people remains an Event candidate until a user creates/approves the role.
ALTER TABLE site_roles ADD COLUMN epistemic_type TEXT NOT NULL DEFAULT 'fact'
    CHECK (epistemic_type IN ('fact','claim','inference','decision','recommendation'));
ALTER TABLE site_roles ADD COLUMN review_status TEXT NOT NULL DEFAULT 'approved'
    CHECK (review_status IN ('pending','approved','corrected','held','rejected'));
ALTER TABLE site_roles ADD COLUMN evidence_ref TEXT;
