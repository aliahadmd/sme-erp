-- Python casefold keys retain Unicode-insensitive substring search on SQLite.
ALTER TABLE crm_contacts ADD COLUMN _name_fold TEXT NOT NULL DEFAULT '';
ALTER TABLE crm_contacts ADD COLUMN _code_fold TEXT NOT NULL DEFAULT '';
ALTER TABLE crm_contacts ADD COLUMN _legal_fold TEXT NOT NULL DEFAULT '';
ALTER TABLE catalog_products ADD COLUMN _name_fold TEXT NOT NULL DEFAULT '';
ALTER TABLE catalog_products ADD COLUMN _sku_fold TEXT NOT NULL DEFAULT '';
