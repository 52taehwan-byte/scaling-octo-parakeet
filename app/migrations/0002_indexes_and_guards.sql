CREATE INDEX sites_workspace_status_idx
ON sites(workspace_id, business_status, updated_at);

CREATE INDEX persons_workspace_name_idx
ON persons(workspace_id, display_name);

CREATE INDEX site_roles_site_idx
ON site_roles(site_id, role_type);

CREATE INDEX events_site_time_idx
ON events(site_id, occurred_at, created_at);

CREATE INDEX tasks_site_status_due_idx
ON tasks(site_id, business_status, due_at);

CREATE INDEX money_items_site_idx
ON money_items(site_id, created_at);

CREATE INDEX money_items_lineage_idx
ON money_items(site_id, lineage_key, created_at);

CREATE INDEX risks_site_status_idx
ON risks(site_id, business_status, severity);

CREATE INDEX reviews_target_idx
ON reviews(target_type, target_id, reviewed_at);

CREATE INDEX audit_workspace_time_idx
ON audit_events(workspace_id, occurred_at);

CREATE TRIGGER audit_events_are_immutable_update
BEFORE UPDATE ON audit_events
BEGIN
    SELECT RAISE(ABORT, 'audit events are immutable');
END;

CREATE TRIGGER audit_events_are_immutable_delete
BEFORE DELETE ON audit_events
BEGIN
    SELECT RAISE(ABORT, 'audit events are immutable');
END;

CREATE TRIGGER reviews_are_immutable_update
BEFORE UPDATE ON reviews
BEGIN
    SELECT RAISE(ABORT, 'reviews are immutable');
END;

CREATE TRIGGER reviews_are_immutable_delete
BEFORE DELETE ON reviews
BEGIN
    SELECT RAISE(ABORT, 'reviews are immutable');
END;
