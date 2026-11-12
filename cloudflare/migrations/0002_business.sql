-- Generated from the original ERP model metadata; review changes before deployment.

-- Decimal columns contain scaled INTEGER coefficients, never SQLite REAL values.

-- Application repositories generate UUIDv7 keys and update timestamps/revisions.

CREATE TABLE "accounting_accounts" (
    "org_id" TEXT NOT NULL CHECK ("org_id" IS NULL OR (length("org_id")=36)),
    "code" TEXT NOT NULL CHECK ("code" IS NULL OR (length("code")<=20)),
    "name" TEXT NOT NULL CHECK ("name" IS NULL OR (length("name")<=100)),
    "type" TEXT NOT NULL CHECK ("type" IS NULL OR (length("type")<=20)),
    "parent_id" TEXT CHECK ("parent_id" IS NULL OR (length("parent_id")=36)),
    "is_system" INTEGER NOT NULL DEFAULT 0 CHECK ("is_system" IS NULL OR ("is_system" IN (0,1))),
    "is_active" INTEGER NOT NULL DEFAULT 1 CHECK ("is_active" IS NULL OR ("is_active" IN (0,1))),
    "created_at" TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    "updated_at" TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    "id" TEXT NOT NULL CHECK ("id" IS NULL OR (length("id")=36)),
    "_revision" INTEGER NOT NULL DEFAULT 0 CHECK ("_revision" BETWEEN 0 AND 9007199254740991),
    PRIMARY KEY ("id"),
    UNIQUE ("org_id","code"),
    FOREIGN KEY ("parent_id") REFERENCES "accounting_accounts" ("id") ON DELETE SET NULL
) STRICT;

CREATE TABLE "accounting_journal_entries" (
    "org_id" TEXT NOT NULL CHECK ("org_id" IS NULL OR (length("org_id")=36)),
    "number" TEXT NOT NULL CHECK ("number" IS NULL OR (length("number")<=30)),
    "entry_date" TEXT NOT NULL,
    "memo" TEXT,
    "source_type" TEXT NOT NULL CHECK ("source_type" IS NULL OR (length("source_type")<=30)),
    "source_id" TEXT CHECK ("source_id" IS NULL OR (length("source_id")=36)),
    "status" TEXT NOT NULL DEFAULT 'posted' CHECK ("status" IS NULL OR (length("status")<=20)),
    "created_by" TEXT CHECK ("created_by" IS NULL OR (length("created_by")=36)),
    "created_at" TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    "updated_at" TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    "id" TEXT NOT NULL CHECK ("id" IS NULL OR (length("id")=36)),
    "_revision" INTEGER NOT NULL DEFAULT 0 CHECK ("_revision" BETWEEN 0 AND 9007199254740991),
    PRIMARY KEY ("id"),
    UNIQUE ("org_id","number")
) STRICT;

CREATE TABLE "accounting_journal_lines" (
    "id" TEXT NOT NULL CHECK ("id" IS NULL OR (length("id")=36)),
    "entry_id" TEXT NOT NULL CHECK ("entry_id" IS NULL OR (length("entry_id")=36)),
    "account_id" TEXT NOT NULL CHECK ("account_id" IS NULL OR (length("account_id")=36)),
    "debit" INTEGER NOT NULL DEFAULT 0 CHECK ("debit" IS NULL OR ("debit" BETWEEN -999999999999999999 AND 999999999999999999)),
    "credit" INTEGER NOT NULL DEFAULT 0 CHECK ("credit" IS NULL OR ("credit" BETWEEN -999999999999999999 AND 999999999999999999)),
    "_revision" INTEGER NOT NULL DEFAULT 0 CHECK ("_revision" BETWEEN 0 AND 9007199254740991),
    PRIMARY KEY ("id"),
    FOREIGN KEY ("account_id") REFERENCES "accounting_accounts" ("id") ON DELETE RESTRICT,
    FOREIGN KEY ("entry_id") REFERENCES "accounting_journal_entries" ("id") ON DELETE CASCADE
) STRICT;

CREATE TABLE "catalog_product_categories" (
    "org_id" TEXT NOT NULL CHECK ("org_id" IS NULL OR (length("org_id")=36)),
    "parent_id" TEXT CHECK ("parent_id" IS NULL OR (length("parent_id")=36)),
    "name" TEXT NOT NULL CHECK ("name" IS NULL OR (length("name")<=100)),
    "created_at" TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    "updated_at" TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    "id" TEXT NOT NULL CHECK ("id" IS NULL OR (length("id")=36)),
    "_revision" INTEGER NOT NULL DEFAULT 0 CHECK ("_revision" BETWEEN 0 AND 9007199254740991),
    PRIMARY KEY ("id"),
    FOREIGN KEY ("parent_id") REFERENCES "catalog_product_categories" ("id") ON DELETE SET NULL
) STRICT;

CREATE TABLE "catalog_products" (
    "org_id" TEXT NOT NULL CHECK ("org_id" IS NULL OR (length("org_id")=36)),
    "sku" TEXT NOT NULL CHECK ("sku" IS NULL OR (length("sku")<=50)),
    "barcode" TEXT CHECK ("barcode" IS NULL OR (length("barcode")<=50)),
    "name" TEXT NOT NULL CHECK ("name" IS NULL OR (length("name")<=200)),
    "description" TEXT,
    "type" TEXT NOT NULL DEFAULT 'goods' CHECK ("type" IS NULL OR (length("type")<=10)),
    "category_id" TEXT CHECK ("category_id" IS NULL OR (length("category_id")=36)),
    "uom_id" TEXT CHECK ("uom_id" IS NULL OR (length("uom_id")=36)),
    "is_purchasable" INTEGER NOT NULL DEFAULT 1 CHECK ("is_purchasable" IS NULL OR ("is_purchasable" IN (0,1))),
    "is_sellable" INTEGER NOT NULL DEFAULT 1 CHECK ("is_sellable" IS NULL OR ("is_sellable" IN (0,1))),
    "track_inventory" INTEGER NOT NULL DEFAULT 1 CHECK ("track_inventory" IS NULL OR ("track_inventory" IN (0,1))),
    "sale_price" INTEGER NOT NULL DEFAULT 0 CHECK ("sale_price" IS NULL OR ("sale_price" BETWEEN -999999999999999999 AND 999999999999999999)),
    "cost_price" INTEGER NOT NULL DEFAULT 0 CHECK ("cost_price" IS NULL OR ("cost_price" BETWEEN -999999999999999999 AND 999999999999999999)),
    "sale_tax_id" TEXT CHECK ("sale_tax_id" IS NULL OR (length("sale_tax_id")=36)),
    "purchase_tax_id" TEXT CHECK ("purchase_tax_id" IS NULL OR (length("purchase_tax_id")=36)),
    "min_stock" INTEGER NOT NULL DEFAULT 0 CHECK ("min_stock" IS NULL OR ("min_stock" BETWEEN -999999999999999999 AND 999999999999999999)),
    "image_key" TEXT CHECK ("image_key" IS NULL OR (length("image_key")<=300)),
    "status" TEXT NOT NULL DEFAULT 'active' CHECK ("status" IS NULL OR (length("status")<=20)),
    "created_at" TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    "updated_at" TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    "id" TEXT NOT NULL CHECK ("id" IS NULL OR (length("id")=36)),
    "_revision" INTEGER NOT NULL DEFAULT 0 CHECK ("_revision" BETWEEN 0 AND 9007199254740991),
    PRIMARY KEY ("id"),
    UNIQUE ("org_id","sku"),
    FOREIGN KEY ("category_id") REFERENCES "catalog_product_categories" ("id") ON DELETE SET NULL,
    FOREIGN KEY ("purchase_tax_id") REFERENCES "catalog_taxes" ("id") ON DELETE SET NULL,
    FOREIGN KEY ("sale_tax_id") REFERENCES "catalog_taxes" ("id") ON DELETE SET NULL,
    FOREIGN KEY ("uom_id") REFERENCES "catalog_uoms" ("id") ON DELETE SET NULL
) STRICT;

CREATE TABLE "catalog_taxes" (
    "org_id" TEXT NOT NULL CHECK ("org_id" IS NULL OR (length("org_id")=36)),
    "code" TEXT NOT NULL CHECK ("code" IS NULL OR (length("code")<=20)),
    "name" TEXT NOT NULL CHECK ("name" IS NULL OR (length("name")<=100)),
    "rate_pct" INTEGER NOT NULL CHECK ("rate_pct" IS NULL OR ("rate_pct" BETWEEN -99999 AND 99999)),
    "applies_to" TEXT NOT NULL DEFAULT 'both' CHECK ("applies_to" IS NULL OR (length("applies_to")<=10)),
    "is_default_sale" INTEGER NOT NULL DEFAULT 0 CHECK ("is_default_sale" IS NULL OR ("is_default_sale" IN (0,1))),
    "is_default_purchase" INTEGER NOT NULL DEFAULT 0 CHECK ("is_default_purchase" IS NULL OR ("is_default_purchase" IN (0,1))),
    "created_at" TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    "updated_at" TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    "id" TEXT NOT NULL CHECK ("id" IS NULL OR (length("id")=36)),
    "_revision" INTEGER NOT NULL DEFAULT 0 CHECK ("_revision" BETWEEN 0 AND 9007199254740991),
    PRIMARY KEY ("id"),
    UNIQUE ("org_id","code")
) STRICT;

CREATE TABLE "catalog_uoms" (
    "org_id" TEXT NOT NULL CHECK ("org_id" IS NULL OR (length("org_id")=36)),
    "code" TEXT NOT NULL CHECK ("code" IS NULL OR (length("code")<=20)),
    "name" TEXT NOT NULL CHECK ("name" IS NULL OR (length("name")<=100)),
    "created_at" TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    "updated_at" TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    "id" TEXT NOT NULL CHECK ("id" IS NULL OR (length("id")=36)),
    "_revision" INTEGER NOT NULL DEFAULT 0 CHECK ("_revision" BETWEEN 0 AND 9007199254740991),
    PRIMARY KEY ("id"),
    UNIQUE ("org_id","code")
) STRICT;

CREATE TABLE "core_ai_drafts" (
    "org_id" TEXT NOT NULL CHECK ("org_id" IS NULL OR (length("org_id")=36)),
    "entity_type" TEXT NOT NULL CHECK ("entity_type" IS NULL OR (length("entity_type")<=50)),
    "entity_id" TEXT NOT NULL CHECK ("entity_id" IS NULL OR (length("entity_id")=36)),
    "field" TEXT NOT NULL CHECK ("field" IS NULL OR (length("field")<=50)),
    "draft_text" TEXT NOT NULL,
    "model" TEXT CHECK ("model" IS NULL OR (length("model")<=100)),
    "status" TEXT NOT NULL DEFAULT 'pending' CHECK ("status" IS NULL OR (length("status")<=20)),
    "created_at" TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    "updated_at" TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    "id" TEXT NOT NULL CHECK ("id" IS NULL OR (length("id")=36)),
    "_revision" INTEGER NOT NULL DEFAULT 0 CHECK ("_revision" BETWEEN 0 AND 9007199254740991),
    PRIMARY KEY ("id")
) STRICT;

CREATE TABLE "core_audit_logs" (
    "id" TEXT NOT NULL CHECK ("id" IS NULL OR (length("id")=36)),
    "org_id" TEXT CHECK ("org_id" IS NULL OR (length("org_id")=36)),
    "user_id" TEXT CHECK ("user_id" IS NULL OR (length("user_id")=36)),
    "action" TEXT NOT NULL CHECK ("action" IS NULL OR (length("action")<=50)),
    "entity_type" TEXT NOT NULL CHECK ("entity_type" IS NULL OR (length("entity_type")<=100)),
    "entity_id" TEXT CHECK ("entity_id" IS NULL OR (length("entity_id")<=64)),
    "before" TEXT CHECK ("before" IS NULL OR (json_valid("before"))),
    "after" TEXT CHECK ("after" IS NULL OR (json_valid("after"))),
    "ip" TEXT CHECK ("ip" IS NULL OR (length("ip")<=64)),
    "created_at" TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    "_revision" INTEGER NOT NULL DEFAULT 0 CHECK ("_revision" BETWEEN 0 AND 9007199254740991),
    PRIMARY KEY ("id")
) STRICT;

CREATE TABLE "core_branches" (
    "org_id" TEXT NOT NULL CHECK ("org_id" IS NULL OR (length("org_id")=36)),
    "code" TEXT NOT NULL CHECK ("code" IS NULL OR (length("code")<=20)),
    "name" TEXT NOT NULL CHECK ("name" IS NULL OR (length("name")<=200)),
    "address" TEXT,
    "is_active" INTEGER NOT NULL DEFAULT 1 CHECK ("is_active" IS NULL OR ("is_active" IN (0,1))),
    "created_at" TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    "updated_at" TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    "id" TEXT NOT NULL CHECK ("id" IS NULL OR (length("id")=36)),
    "_revision" INTEGER NOT NULL DEFAULT 0 CHECK ("_revision" BETWEEN 0 AND 9007199254740991),
    PRIMARY KEY ("id"),
    FOREIGN KEY ("org_id") REFERENCES "core_organizations" ("id") ON DELETE CASCADE
) STRICT;

CREATE TABLE "core_currencies" (
    "org_id" TEXT NOT NULL CHECK ("org_id" IS NULL OR (length("org_id")=36)),
    "code" TEXT NOT NULL CHECK ("code" IS NULL OR (length("code")<=3)),
    "name" TEXT NOT NULL CHECK ("name" IS NULL OR (length("name")<=50)),
    "created_at" TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    "updated_at" TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    "id" TEXT NOT NULL CHECK ("id" IS NULL OR (length("id")=36)),
    "_revision" INTEGER NOT NULL DEFAULT 0 CHECK ("_revision" BETWEEN 0 AND 9007199254740991),
    PRIMARY KEY ("id"),
    UNIQUE ("org_id","code")
) STRICT;

CREATE TABLE "core_document_sequences" (
    "id" TEXT NOT NULL CHECK ("id" IS NULL OR (length("id")=36)),
    "org_id" TEXT NOT NULL CHECK ("org_id" IS NULL OR (length("org_id")=36)),
    "entity" TEXT NOT NULL CHECK ("entity" IS NULL OR (length("entity")<=50)),
    "year" INTEGER NOT NULL,
    "last_number" INTEGER NOT NULL DEFAULT 0,
    "_revision" INTEGER NOT NULL DEFAULT 0 CHECK ("_revision" BETWEEN 0 AND 9007199254740991),
    PRIMARY KEY ("id"),
    UNIQUE ("org_id","entity","year")
) STRICT;

CREATE TABLE "core_fx_rates" (
    "org_id" TEXT NOT NULL CHECK ("org_id" IS NULL OR (length("org_id")=36)),
    "currency" TEXT NOT NULL CHECK ("currency" IS NULL OR (length("currency")<=3)),
    "rate_date" TEXT NOT NULL,
    "rate" INTEGER NOT NULL CHECK ("rate" IS NULL OR ("rate" BETWEEN -999999999999999999 AND 999999999999999999)),
    "created_at" TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    "updated_at" TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    "id" TEXT NOT NULL CHECK ("id" IS NULL OR (length("id")=36)),
    "_revision" INTEGER NOT NULL DEFAULT 0 CHECK ("_revision" BETWEEN 0 AND 9007199254740991),
    PRIMARY KEY ("id"),
    UNIQUE ("org_id","currency","rate_date")
) STRICT;

CREATE TABLE "core_notifications" (
    "user_id" TEXT NOT NULL CHECK ("user_id" IS NULL OR (length("user_id")=36)),
    "type" TEXT NOT NULL CHECK ("type" IS NULL OR (length("type")<=50)),
    "title" TEXT NOT NULL CHECK ("title" IS NULL OR (length("title")<=200)),
    "body" TEXT,
    "link" TEXT CHECK ("link" IS NULL OR (length("link")<=300)),
    "payload" TEXT NOT NULL DEFAULT '{}' CHECK ("payload" IS NULL OR (json_valid("payload"))),
    "read_at" TEXT,
    "created_at" TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    "id" TEXT NOT NULL CHECK ("id" IS NULL OR (length("id")=36)),
    "_revision" INTEGER NOT NULL DEFAULT 0 CHECK ("_revision" BETWEEN 0 AND 9007199254740991),
    PRIMARY KEY ("id"),
    FOREIGN KEY ("user_id") REFERENCES "core_users" ("id") ON DELETE CASCADE
) STRICT;

CREATE TABLE "core_organizations" (
    "name" TEXT NOT NULL CHECK ("name" IS NULL OR (length("name")<=200)),
    "legal_name" TEXT CHECK ("legal_name" IS NULL OR (length("legal_name")<=200)),
    "tax_id" TEXT CHECK ("tax_id" IS NULL OR (length("tax_id")<=50)),
    "base_currency" TEXT NOT NULL DEFAULT 'USD' CHECK ("base_currency" IS NULL OR (length("base_currency")<=3)),
    "email" TEXT CHECK ("email" IS NULL OR (length("email")<=200)),
    "phone" TEXT CHECK ("phone" IS NULL OR (length("phone")<=50)),
    "address_line1" TEXT CHECK ("address_line1" IS NULL OR (length("address_line1")<=200)),
    "address_line2" TEXT CHECK ("address_line2" IS NULL OR (length("address_line2")<=200)),
    "city" TEXT CHECK ("city" IS NULL OR (length("city")<=100)),
    "country" TEXT CHECK ("country" IS NULL OR (length("country")<=2)),
    "created_at" TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    "updated_at" TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    "id" TEXT NOT NULL CHECK ("id" IS NULL OR (length("id")=36)),
    "_revision" INTEGER NOT NULL DEFAULT 0 CHECK ("_revision" BETWEEN 0 AND 9007199254740991),
    PRIMARY KEY ("id")
) STRICT;

CREATE TABLE "core_permissions" (
    "code" TEXT NOT NULL CHECK ("code" IS NULL OR (length("code")<=100)),
    "module" TEXT NOT NULL CHECK ("module" IS NULL OR (length("module")<=50)),
    "action" TEXT NOT NULL CHECK ("action" IS NULL OR (length("action")<=50)),
    "description" TEXT,
    "id" TEXT NOT NULL CHECK ("id" IS NULL OR (length("id")=36)),
    "_revision" INTEGER NOT NULL DEFAULT 0 CHECK ("_revision" BETWEEN 0 AND 9007199254740991),
    PRIMARY KEY ("id")
) STRICT;

CREATE TABLE "core_role_permissions" (
    "role_id" TEXT NOT NULL CHECK ("role_id" IS NULL OR (length("role_id")=36)),
    "permission_id" TEXT NOT NULL CHECK ("permission_id" IS NULL OR (length("permission_id")=36)),
    "_revision" INTEGER NOT NULL DEFAULT 0 CHECK ("_revision" BETWEEN 0 AND 9007199254740991),
    PRIMARY KEY ("role_id","permission_id"),
    FOREIGN KEY ("permission_id") REFERENCES "core_permissions" ("id") ON DELETE CASCADE,
    FOREIGN KEY ("role_id") REFERENCES "core_roles" ("id") ON DELETE CASCADE
) STRICT;

CREATE TABLE "core_roles" (
    "code" TEXT NOT NULL CHECK ("code" IS NULL OR (length("code")<=50)),
    "name" TEXT NOT NULL CHECK ("name" IS NULL OR (length("name")<=100)),
    "description" TEXT,
    "is_system" INTEGER NOT NULL DEFAULT 0 CHECK ("is_system" IS NULL OR ("is_system" IN (0,1))),
    "created_at" TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    "updated_at" TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    "id" TEXT NOT NULL CHECK ("id" IS NULL OR (length("id")=36)),
    "_revision" INTEGER NOT NULL DEFAULT 0 CHECK ("_revision" BETWEEN 0 AND 9007199254740991),
    PRIMARY KEY ("id"),
    UNIQUE ("code")
) STRICT;

CREATE TABLE "core_settings" (
    "org_id" TEXT NOT NULL CHECK ("org_id" IS NULL OR (length("org_id")=36)),
    "key" TEXT NOT NULL CHECK ("key" IS NULL OR (length("key")<=100)),
    "value" TEXT NOT NULL DEFAULT '{}' CHECK ("value" IS NULL OR (json_valid("value"))),
    "created_at" TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    "updated_at" TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    "id" TEXT NOT NULL CHECK ("id" IS NULL OR (length("id")=36)),
    "_revision" INTEGER NOT NULL DEFAULT 0 CHECK ("_revision" BETWEEN 0 AND 9007199254740991),
    PRIMARY KEY ("id"),
    UNIQUE ("org_id","key"),
    FOREIGN KEY ("org_id") REFERENCES "core_organizations" ("id") ON DELETE CASCADE
) STRICT;

CREATE TABLE "core_user_roles" (
    "user_id" TEXT NOT NULL CHECK ("user_id" IS NULL OR (length("user_id")=36)),
    "role_id" TEXT NOT NULL CHECK ("role_id" IS NULL OR (length("role_id")=36)),
    "_revision" INTEGER NOT NULL DEFAULT 0 CHECK ("_revision" BETWEEN 0 AND 9007199254740991),
    PRIMARY KEY ("user_id","role_id"),
    FOREIGN KEY ("role_id") REFERENCES "core_roles" ("id") ON DELETE CASCADE,
    FOREIGN KEY ("user_id") REFERENCES "core_users" ("id") ON DELETE CASCADE
) STRICT;

CREATE TABLE "core_users" (
    "email" TEXT NOT NULL CHECK ("email" IS NULL OR (length("email")<=255)),
    "hashed_password" TEXT NOT NULL CHECK ("hashed_password" IS NULL OR (length("hashed_password")<=255)),
    "full_name" TEXT NOT NULL CHECK ("full_name" IS NULL OR (length("full_name")<=200)),
    "is_active" INTEGER NOT NULL DEFAULT 1 CHECK ("is_active" IS NULL OR ("is_active" IN (0,1))),
    "is_superuser" INTEGER NOT NULL DEFAULT 0 CHECK ("is_superuser" IS NULL OR ("is_superuser" IN (0,1))),
    "default_branch_id" TEXT CHECK ("default_branch_id" IS NULL OR (length("default_branch_id")=36)),
    "email_prefs" TEXT NOT NULL DEFAULT '{}' CHECK ("email_prefs" IS NULL OR (json_valid("email_prefs"))),
    "last_login_at" TEXT,
    "created_at" TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    "updated_at" TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    "id" TEXT NOT NULL CHECK ("id" IS NULL OR (length("id")=36)),
    "_revision" INTEGER NOT NULL DEFAULT 0 CHECK ("_revision" BETWEEN 0 AND 9007199254740991),
    PRIMARY KEY ("id"),
    FOREIGN KEY ("default_branch_id") REFERENCES "core_branches" ("id") ON DELETE SET NULL
) STRICT;

CREATE TABLE "crm_contacts" (
    "org_id" TEXT NOT NULL CHECK ("org_id" IS NULL OR (length("org_id")=36)),
    "code" TEXT NOT NULL CHECK ("code" IS NULL OR (length("code")<=30)),
    "kind" TEXT NOT NULL DEFAULT 'company' CHECK ("kind" IS NULL OR (length("kind")<=10)),
    "name" TEXT NOT NULL CHECK ("name" IS NULL OR (length("name")<=200)),
    "legal_name" TEXT CHECK ("legal_name" IS NULL OR (length("legal_name")<=200)),
    "tax_id" TEXT CHECK ("tax_id" IS NULL OR (length("tax_id")<=50)),
    "is_customer" INTEGER NOT NULL DEFAULT 0 CHECK ("is_customer" IS NULL OR ("is_customer" IN (0,1))),
    "is_supplier" INTEGER NOT NULL DEFAULT 0 CHECK ("is_supplier" IS NULL OR ("is_supplier" IN (0,1))),
    "emails" TEXT NOT NULL DEFAULT '[]' CHECK ("emails" IS NULL OR (json_valid("emails"))),
    "phones" TEXT NOT NULL DEFAULT '[]' CHECK ("phones" IS NULL OR (json_valid("phones"))),
    "addresses" TEXT NOT NULL DEFAULT '[]' CHECK ("addresses" IS NULL OR (json_valid("addresses"))),
    "currency" TEXT NOT NULL DEFAULT 'USD' CHECK ("currency" IS NULL OR (length("currency")<=3)),
    "payment_terms_days" INTEGER NOT NULL DEFAULT 30,
    "credit_limit" INTEGER NOT NULL DEFAULT 0 CHECK ("credit_limit" IS NULL OR ("credit_limit" BETWEEN -999999999999999999 AND 999999999999999999)),
    "tags" TEXT NOT NULL DEFAULT '[]' CHECK ("tags" IS NULL OR (json_valid("tags"))),
    "owner_user_id" TEXT CHECK ("owner_user_id" IS NULL OR (length("owner_user_id")=36)),
    "notes" TEXT,
    "status" TEXT NOT NULL DEFAULT 'active' CHECK ("status" IS NULL OR (length("status")<=20)),
    "created_at" TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    "updated_at" TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    "id" TEXT NOT NULL CHECK ("id" IS NULL OR (length("id")=36)),
    "_revision" INTEGER NOT NULL DEFAULT 0 CHECK ("_revision" BETWEEN 0 AND 9007199254740991),
    PRIMARY KEY ("id"),
    UNIQUE ("org_id","code"),
    FOREIGN KEY ("owner_user_id") REFERENCES "core_users" ("id") ON DELETE SET NULL
) STRICT;

CREATE TABLE "hr_departments" (
    "org_id" TEXT NOT NULL CHECK ("org_id" IS NULL OR (length("org_id")=36)),
    "name" TEXT NOT NULL CHECK ("name" IS NULL OR (length("name")<=100)),
    "manager_id" TEXT CHECK ("manager_id" IS NULL OR (length("manager_id")=36)),
    "created_at" TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    "updated_at" TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    "id" TEXT NOT NULL CHECK ("id" IS NULL OR (length("id")=36)),
    "_revision" INTEGER NOT NULL DEFAULT 0 CHECK ("_revision" BETWEEN 0 AND 9007199254740991),
    PRIMARY KEY ("id")
) STRICT;

CREATE TABLE "hr_employees" (
    "org_id" TEXT NOT NULL CHECK ("org_id" IS NULL OR (length("org_id")=36)),
    "number" TEXT NOT NULL CHECK ("number" IS NULL OR (length("number")<=20)),
    "full_name" TEXT NOT NULL CHECK ("full_name" IS NULL OR (length("full_name")<=200)),
    "work_email" TEXT CHECK ("work_email" IS NULL OR (length("work_email")<=255)),
    "user_id" TEXT CHECK ("user_id" IS NULL OR (length("user_id")=36)),
    "department_id" TEXT CHECK ("department_id" IS NULL OR (length("department_id")=36)),
    "position" TEXT CHECK ("position" IS NULL OR (length("position")<=100)),
    "hired_at" TEXT,
    "terminated_at" TEXT,
    "status" TEXT NOT NULL DEFAULT 'active' CHECK ("status" IS NULL OR (length("status")<=20)),
    "created_at" TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    "updated_at" TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    "id" TEXT NOT NULL CHECK ("id" IS NULL OR (length("id")=36)),
    "_revision" INTEGER NOT NULL DEFAULT 0 CHECK ("_revision" BETWEEN 0 AND 9007199254740991),
    PRIMARY KEY ("id"),
    UNIQUE ("org_id","number"),
    UNIQUE ("user_id"),
    FOREIGN KEY ("department_id") REFERENCES "hr_departments" ("id") ON DELETE SET NULL,
    FOREIGN KEY ("user_id") REFERENCES "core_users" ("id") ON DELETE SET NULL
) STRICT;

CREATE TABLE "hr_leave_requests" (
    "id" TEXT NOT NULL CHECK ("id" IS NULL OR (length("id")=36)),
    "org_id" TEXT NOT NULL CHECK ("org_id" IS NULL OR (length("org_id")=36)),
    "employee_id" TEXT NOT NULL CHECK ("employee_id" IS NULL OR (length("employee_id")=36)),
    "type_id" TEXT NOT NULL CHECK ("type_id" IS NULL OR (length("type_id")=36)),
    "date_from" TEXT NOT NULL,
    "date_to" TEXT NOT NULL,
    "days" INTEGER NOT NULL CHECK ("days" IS NULL OR ("days" BETWEEN -99999 AND 99999)),
    "reason" TEXT CHECK ("reason" IS NULL OR (length("reason")<=300)),
    "status" TEXT NOT NULL DEFAULT 'pending' CHECK ("status" IS NULL OR (length("status")<=20)),
    "approver_id" TEXT CHECK ("approver_id" IS NULL OR (length("approver_id")=36)),
    "decided_at" TEXT,
    "created_at" TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    "updated_at" TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    "_revision" INTEGER NOT NULL DEFAULT 0 CHECK ("_revision" BETWEEN 0 AND 9007199254740991),
    PRIMARY KEY ("id"),
    FOREIGN KEY ("approver_id") REFERENCES "core_users" ("id") ON DELETE SET NULL,
    FOREIGN KEY ("employee_id") REFERENCES "hr_employees" ("id") ON DELETE CASCADE,
    FOREIGN KEY ("type_id") REFERENCES "hr_leave_types" ("id") ON DELETE RESTRICT
) STRICT;

CREATE TABLE "hr_leave_types" (
    "org_id" TEXT NOT NULL CHECK ("org_id" IS NULL OR (length("org_id")=36)),
    "name" TEXT NOT NULL CHECK ("name" IS NULL OR (length("name")<=50)),
    "days_per_year" INTEGER NOT NULL DEFAULT 0 CHECK ("days_per_year" IS NULL OR ("days_per_year" BETWEEN -99999 AND 99999)),
    "accrues" INTEGER NOT NULL DEFAULT 0 CHECK ("accrues" IS NULL OR ("accrues" IN (0,1))),
    "created_at" TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    "updated_at" TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    "id" TEXT NOT NULL CHECK ("id" IS NULL OR (length("id")=36)),
    "_revision" INTEGER NOT NULL DEFAULT 0 CHECK ("_revision" BETWEEN 0 AND 9007199254740991),
    PRIMARY KEY ("id"),
    UNIQUE ("org_id","name")
) STRICT;

CREATE TABLE "inventory_adjustment_lines" (
    "adjustment_id" TEXT NOT NULL CHECK ("adjustment_id" IS NULL OR (length("adjustment_id")=36)),
    "id" TEXT NOT NULL CHECK ("id" IS NULL OR (length("id")=36)),
    "product_id" TEXT NOT NULL CHECK ("product_id" IS NULL OR (length("product_id")=36)),
    "qty" INTEGER NOT NULL CHECK ("qty" IS NULL OR ("qty" BETWEEN -999999999999999999 AND 999999999999999999)),
    "unit_cost" INTEGER NOT NULL DEFAULT 0 CHECK ("unit_cost" IS NULL OR ("unit_cost" BETWEEN -999999999999999999 AND 999999999999999999)),
    "_revision" INTEGER NOT NULL DEFAULT 0 CHECK ("_revision" BETWEEN 0 AND 9007199254740991),
    PRIMARY KEY ("id"),
    FOREIGN KEY ("adjustment_id") REFERENCES "inventory_adjustments" ("id") ON DELETE CASCADE
) STRICT;

CREATE TABLE "inventory_adjustments" (
    "org_id" TEXT NOT NULL CHECK ("org_id" IS NULL OR (length("org_id")=36)),
    "number" TEXT NOT NULL CHECK ("number" IS NULL OR (length("number")<=30)),
    "warehouse_id" TEXT NOT NULL CHECK ("warehouse_id" IS NULL OR (length("warehouse_id")=36)),
    "reason" TEXT NOT NULL CHECK ("reason" IS NULL OR (length("reason")<=30)),
    "notes" TEXT,
    "status" TEXT NOT NULL DEFAULT 'draft' CHECK ("status" IS NULL OR (length("status")<=20)),
    "posted_at" TEXT,
    "posted_by" TEXT CHECK ("posted_by" IS NULL OR (length("posted_by")=36)),
    "created_at" TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    "updated_at" TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    "id" TEXT NOT NULL CHECK ("id" IS NULL OR (length("id")=36)),
    "_revision" INTEGER NOT NULL DEFAULT 0 CHECK ("_revision" BETWEEN 0 AND 9007199254740991),
    PRIMARY KEY ("id"),
    UNIQUE ("org_id","number"),
    FOREIGN KEY ("warehouse_id") REFERENCES "inventory_warehouses" ("id") ON DELETE RESTRICT
) STRICT;

CREATE TABLE "inventory_deliveries" (
    "org_id" TEXT NOT NULL CHECK ("org_id" IS NULL OR (length("org_id")=36)),
    "number" TEXT NOT NULL CHECK ("number" IS NULL OR (length("number")<=30)),
    "warehouse_id" TEXT NOT NULL CHECK ("warehouse_id" IS NULL OR (length("warehouse_id")=36)),
    "source_type" TEXT CHECK ("source_type" IS NULL OR (length("source_type")<=30)),
    "source_id" TEXT CHECK ("source_id" IS NULL OR (length("source_id")=36)),
    "source_number" TEXT CHECK ("source_number" IS NULL OR (length("source_number")<=30)),
    "status" TEXT NOT NULL DEFAULT 'draft' CHECK ("status" IS NULL OR (length("status")<=20)),
    "notes" TEXT,
    "posted_at" TEXT,
    "posted_by" TEXT CHECK ("posted_by" IS NULL OR (length("posted_by")=36)),
    "created_at" TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    "updated_at" TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    "id" TEXT NOT NULL CHECK ("id" IS NULL OR (length("id")=36)),
    "_revision" INTEGER NOT NULL DEFAULT 0 CHECK ("_revision" BETWEEN 0 AND 9007199254740991),
    PRIMARY KEY ("id"),
    UNIQUE ("org_id","number"),
    FOREIGN KEY ("warehouse_id") REFERENCES "inventory_warehouses" ("id") ON DELETE RESTRICT
) STRICT;

CREATE TABLE "inventory_delivery_lines" (
    "delivery_id" TEXT NOT NULL CHECK ("delivery_id" IS NULL OR (length("delivery_id")=36)),
    "id" TEXT NOT NULL CHECK ("id" IS NULL OR (length("id")=36)),
    "product_id" TEXT NOT NULL CHECK ("product_id" IS NULL OR (length("product_id")=36)),
    "qty" INTEGER NOT NULL CHECK ("qty" IS NULL OR ("qty" BETWEEN -999999999999999999 AND 999999999999999999)),
    "unit_cost" INTEGER NOT NULL DEFAULT 0 CHECK ("unit_cost" IS NULL OR ("unit_cost" BETWEEN -999999999999999999 AND 999999999999999999)),
    "_revision" INTEGER NOT NULL DEFAULT 0 CHECK ("_revision" BETWEEN 0 AND 9007199254740991),
    PRIMARY KEY ("id"),
    FOREIGN KEY ("delivery_id") REFERENCES "inventory_deliveries" ("id") ON DELETE CASCADE
) STRICT;

CREATE TABLE "inventory_receipt_lines" (
    "receipt_id" TEXT NOT NULL CHECK ("receipt_id" IS NULL OR (length("receipt_id")=36)),
    "id" TEXT NOT NULL CHECK ("id" IS NULL OR (length("id")=36)),
    "product_id" TEXT NOT NULL CHECK ("product_id" IS NULL OR (length("product_id")=36)),
    "qty" INTEGER NOT NULL CHECK ("qty" IS NULL OR ("qty" BETWEEN -999999999999999999 AND 999999999999999999)),
    "unit_cost" INTEGER NOT NULL DEFAULT 0 CHECK ("unit_cost" IS NULL OR ("unit_cost" BETWEEN -999999999999999999 AND 999999999999999999)),
    "_revision" INTEGER NOT NULL DEFAULT 0 CHECK ("_revision" BETWEEN 0 AND 9007199254740991),
    PRIMARY KEY ("id"),
    FOREIGN KEY ("receipt_id") REFERENCES "inventory_receipts" ("id") ON DELETE CASCADE
) STRICT;

CREATE TABLE "inventory_receipts" (
    "org_id" TEXT NOT NULL CHECK ("org_id" IS NULL OR (length("org_id")=36)),
    "number" TEXT NOT NULL CHECK ("number" IS NULL OR (length("number")<=30)),
    "warehouse_id" TEXT NOT NULL CHECK ("warehouse_id" IS NULL OR (length("warehouse_id")=36)),
    "source_type" TEXT CHECK ("source_type" IS NULL OR (length("source_type")<=30)),
    "source_id" TEXT CHECK ("source_id" IS NULL OR (length("source_id")=36)),
    "source_number" TEXT CHECK ("source_number" IS NULL OR (length("source_number")<=30)),
    "status" TEXT NOT NULL DEFAULT 'draft' CHECK ("status" IS NULL OR (length("status")<=20)),
    "notes" TEXT,
    "posted_at" TEXT,
    "posted_by" TEXT CHECK ("posted_by" IS NULL OR (length("posted_by")=36)),
    "created_at" TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    "updated_at" TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    "id" TEXT NOT NULL CHECK ("id" IS NULL OR (length("id")=36)),
    "_revision" INTEGER NOT NULL DEFAULT 0 CHECK ("_revision" BETWEEN 0 AND 9007199254740991),
    PRIMARY KEY ("id"),
    UNIQUE ("org_id","number"),
    FOREIGN KEY ("warehouse_id") REFERENCES "inventory_warehouses" ("id") ON DELETE RESTRICT
) STRICT;

CREATE TABLE "inventory_stock" (
    "id" TEXT NOT NULL CHECK ("id" IS NULL OR (length("id")=36)),
    "org_id" TEXT NOT NULL CHECK ("org_id" IS NULL OR (length("org_id")=36)),
    "product_id" TEXT NOT NULL CHECK ("product_id" IS NULL OR (length("product_id")=36)),
    "warehouse_id" TEXT NOT NULL CHECK ("warehouse_id" IS NULL OR (length("warehouse_id")=36)),
    "qty_on_hand" INTEGER NOT NULL DEFAULT 0 CHECK ("qty_on_hand" IS NULL OR ("qty_on_hand" BETWEEN -999999999999999999 AND 999999999999999999)),
    "avg_cost" INTEGER NOT NULL DEFAULT 0 CHECK ("avg_cost" IS NULL OR ("avg_cost" BETWEEN -999999999999999999 AND 999999999999999999)),
    "last_move_at" TEXT,
    "created_at" TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    "updated_at" TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    "_revision" INTEGER NOT NULL DEFAULT 0 CHECK ("_revision" BETWEEN 0 AND 9007199254740991),
    PRIMARY KEY ("id"),
    UNIQUE ("org_id","product_id","warehouse_id")
) STRICT;

CREATE TABLE "inventory_stock_moves" (
    "id" TEXT NOT NULL CHECK ("id" IS NULL OR (length("id")=36)),
    "org_id" TEXT NOT NULL CHECK ("org_id" IS NULL OR (length("org_id")=36)),
    "product_id" TEXT NOT NULL CHECK ("product_id" IS NULL OR (length("product_id")=36)),
    "warehouse_id" TEXT NOT NULL CHECK ("warehouse_id" IS NULL OR (length("warehouse_id")=36)),
    "qty" INTEGER NOT NULL CHECK ("qty" IS NULL OR ("qty" BETWEEN -999999999999999999 AND 999999999999999999)),
    "move_type" TEXT NOT NULL CHECK ("move_type" IS NULL OR (length("move_type")<=20)),
    "ref_type" TEXT CHECK ("ref_type" IS NULL OR (length("ref_type")<=50)),
    "ref_id" TEXT CHECK ("ref_id" IS NULL OR (length("ref_id")=36)),
    "ref_number" TEXT CHECK ("ref_number" IS NULL OR (length("ref_number")<=30)),
    "unit_cost" INTEGER NOT NULL DEFAULT 0 CHECK ("unit_cost" IS NULL OR ("unit_cost" BETWEEN -999999999999999999 AND 999999999999999999)),
    "cogs" INTEGER NOT NULL DEFAULT 0 CHECK ("cogs" IS NULL OR ("cogs" BETWEEN -999999999999999999 AND 999999999999999999)),
    "reason" TEXT,
    "moved_at" TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    "created_by" TEXT CHECK ("created_by" IS NULL OR (length("created_by")=36)),
    "reverses_move_id" TEXT CHECK ("reverses_move_id" IS NULL OR (length("reverses_move_id")=36)),
    "_revision" INTEGER NOT NULL DEFAULT 0 CHECK ("_revision" BETWEEN 0 AND 9007199254740991),
    PRIMARY KEY ("id")
) STRICT;

CREATE TABLE "inventory_warehouses" (
    "org_id" TEXT NOT NULL CHECK ("org_id" IS NULL OR (length("org_id")=36)),
    "code" TEXT NOT NULL CHECK ("code" IS NULL OR (length("code")<=20)),
    "name" TEXT NOT NULL CHECK ("name" IS NULL OR (length("name")<=100)),
    "branch_id" TEXT CHECK ("branch_id" IS NULL OR (length("branch_id")=36)),
    "is_default" INTEGER NOT NULL DEFAULT 0 CHECK ("is_default" IS NULL OR ("is_default" IN (0,1))),
    "created_at" TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    "updated_at" TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    "id" TEXT NOT NULL CHECK ("id" IS NULL OR (length("id")=36)),
    "_revision" INTEGER NOT NULL DEFAULT 0 CHECK ("_revision" BETWEEN 0 AND 9007199254740991),
    PRIMARY KEY ("id"),
    UNIQUE ("org_id","code"),
    FOREIGN KEY ("branch_id") REFERENCES "core_branches" ("id") ON DELETE SET NULL
) STRICT;

CREATE TABLE "invoicing_invoice_lines" (
    "id" TEXT NOT NULL CHECK ("id" IS NULL OR (length("id")=36)),
    "invoice_id" TEXT NOT NULL CHECK ("invoice_id" IS NULL OR (length("invoice_id")=36)),
    "position" INTEGER NOT NULL DEFAULT 0,
    "product_id" TEXT CHECK ("product_id" IS NULL OR (length("product_id")=36)),
    "product_name" TEXT CHECK ("product_name" IS NULL OR (length("product_name")<=200)),
    "description" TEXT,
    "qty" INTEGER NOT NULL CHECK ("qty" IS NULL OR ("qty" BETWEEN -999999999999999999 AND 999999999999999999)),
    "uom_code" TEXT CHECK ("uom_code" IS NULL OR (length("uom_code")<=20)),
    "unit_price" INTEGER NOT NULL CHECK ("unit_price" IS NULL OR ("unit_price" BETWEEN -999999999999999999 AND 999999999999999999)),
    "discount_pct" INTEGER NOT NULL DEFAULT 0 CHECK ("discount_pct" IS NULL OR ("discount_pct" BETWEEN -99999 AND 99999)),
    "tax_id" TEXT CHECK ("tax_id" IS NULL OR (length("tax_id")=36)),
    "tax_rate_pct" INTEGER NOT NULL DEFAULT 0 CHECK ("tax_rate_pct" IS NULL OR ("tax_rate_pct" BETWEEN -99999 AND 99999)),
    "line_subtotal" INTEGER NOT NULL DEFAULT 0 CHECK ("line_subtotal" IS NULL OR ("line_subtotal" BETWEEN -999999999999999999 AND 999999999999999999)),
    "line_tax" INTEGER NOT NULL DEFAULT 0 CHECK ("line_tax" IS NULL OR ("line_tax" BETWEEN -999999999999999999 AND 999999999999999999)),
    "line_total" INTEGER NOT NULL DEFAULT 0 CHECK ("line_total" IS NULL OR ("line_total" BETWEEN -999999999999999999 AND 999999999999999999)),
    "source_line_id" TEXT CHECK ("source_line_id" IS NULL OR (length("source_line_id")=36)),
    "_revision" INTEGER NOT NULL DEFAULT 0 CHECK ("_revision" BETWEEN 0 AND 9007199254740991),
    PRIMARY KEY ("id"),
    FOREIGN KEY ("invoice_id") REFERENCES "invoicing_invoices" ("id") ON DELETE CASCADE,
    FOREIGN KEY ("product_id") REFERENCES "catalog_products" ("id") ON DELETE SET NULL,
    FOREIGN KEY ("tax_id") REFERENCES "catalog_taxes" ("id") ON DELETE SET NULL
) STRICT;

CREATE TABLE "invoicing_invoices" (
    "org_id" TEXT NOT NULL CHECK ("org_id" IS NULL OR (length("org_id")=36)),
    "invoice_type" TEXT NOT NULL CHECK ("invoice_type" IS NULL OR (length("invoice_type")<=12)),
    "original_invoice_id" TEXT CHECK ("original_invoice_id" IS NULL OR (length("original_invoice_id")=36)),
    "applied_credits" INTEGER NOT NULL DEFAULT 0 CHECK ("applied_credits" IS NULL OR ("applied_credits" BETWEEN -999999999999999999 AND 999999999999999999)),
    "number" TEXT CHECK ("number" IS NULL OR (length("number")<=30)),
    "party_id" TEXT CHECK ("party_id" IS NULL OR (length("party_id")=36)),
    "party_name" TEXT CHECK ("party_name" IS NULL OR (length("party_name")<=200)),
    "source_type" TEXT CHECK ("source_type" IS NULL OR (length("source_type")<=30)),
    "source_id" TEXT CHECK ("source_id" IS NULL OR (length("source_id")=36)),
    "source_number" TEXT CHECK ("source_number" IS NULL OR (length("source_number")<=30)),
    "invoice_date" TEXT NOT NULL,
    "due_date" TEXT,
    "currency" TEXT NOT NULL DEFAULT 'USD' CHECK ("currency" IS NULL OR (length("currency")<=3)),
    "status" TEXT NOT NULL DEFAULT 'draft' CHECK ("status" IS NULL OR (length("status")<=20)),
    "subtotal" INTEGER NOT NULL DEFAULT 0 CHECK ("subtotal" IS NULL OR ("subtotal" BETWEEN -999999999999999999 AND 999999999999999999)),
    "discount_total" INTEGER NOT NULL DEFAULT 0 CHECK ("discount_total" IS NULL OR ("discount_total" BETWEEN -999999999999999999 AND 999999999999999999)),
    "tax_total" INTEGER NOT NULL DEFAULT 0 CHECK ("tax_total" IS NULL OR ("tax_total" BETWEEN -999999999999999999 AND 999999999999999999)),
    "total" INTEGER NOT NULL DEFAULT 0 CHECK ("total" IS NULL OR ("total" BETWEEN -999999999999999999 AND 999999999999999999)),
    "fx_rate" INTEGER NOT NULL DEFAULT 100000000 CHECK ("fx_rate" IS NULL OR ("fx_rate" BETWEEN -999999999999999999 AND 999999999999999999)),
    "total_base" INTEGER NOT NULL DEFAULT 0 CHECK ("total_base" IS NULL OR ("total_base" BETWEEN -999999999999999999 AND 999999999999999999)),
    "amount_paid" INTEGER NOT NULL DEFAULT 0 CHECK ("amount_paid" IS NULL OR ("amount_paid" BETWEEN -999999999999999999 AND 999999999999999999)),
    "notes" TEXT,
    "posted_at" TEXT,
    "posted_by" TEXT CHECK ("posted_by" IS NULL OR (length("posted_by")=36)),
    "created_at" TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    "updated_at" TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    "id" TEXT NOT NULL CHECK ("id" IS NULL OR (length("id")=36)),
    "_revision" INTEGER NOT NULL DEFAULT 0 CHECK ("_revision" BETWEEN 0 AND 9007199254740991),
    PRIMARY KEY ("id"),
    UNIQUE ("org_id","number"),
    FOREIGN KEY ("party_id") REFERENCES "crm_contacts" ("id") ON DELETE SET NULL
) STRICT;

CREATE TABLE "invoicing_payment_allocations" (
    "id" TEXT NOT NULL CHECK ("id" IS NULL OR (length("id")=36)),
    "payment_id" TEXT NOT NULL CHECK ("payment_id" IS NULL OR (length("payment_id")=36)),
    "invoice_id" TEXT NOT NULL CHECK ("invoice_id" IS NULL OR (length("invoice_id")=36)),
    "amount" INTEGER NOT NULL CHECK ("amount" IS NULL OR ("amount" BETWEEN -999999999999999999 AND 999999999999999999)),
    "_revision" INTEGER NOT NULL DEFAULT 0 CHECK ("_revision" BETWEEN 0 AND 9007199254740991),
    PRIMARY KEY ("id"),
    FOREIGN KEY ("invoice_id") REFERENCES "invoicing_invoices" ("id") ON DELETE RESTRICT,
    FOREIGN KEY ("payment_id") REFERENCES "invoicing_payments" ("id") ON DELETE CASCADE
) STRICT;

CREATE TABLE "invoicing_payments" (
    "org_id" TEXT NOT NULL CHECK ("org_id" IS NULL OR (length("org_id")=36)),
    "number" TEXT NOT NULL CHECK ("number" IS NULL OR (length("number")<=30)),
    "direction" TEXT NOT NULL CHECK ("direction" IS NULL OR (length("direction")<=10)),
    "party_id" TEXT NOT NULL CHECK ("party_id" IS NULL OR (length("party_id")=36)),
    "party_name" TEXT CHECK ("party_name" IS NULL OR (length("party_name")<=200)),
    "payment_date" TEXT NOT NULL,
    "currency" TEXT NOT NULL DEFAULT 'USD' CHECK ("currency" IS NULL OR (length("currency")<=3)),
    "fx_rate" INTEGER NOT NULL DEFAULT 100000000 CHECK ("fx_rate" IS NULL OR ("fx_rate" BETWEEN -999999999999999999 AND 999999999999999999)),
    "amount" INTEGER NOT NULL CHECK ("amount" IS NULL OR ("amount" BETWEEN -999999999999999999 AND 999999999999999999)),
    "amount_base" INTEGER NOT NULL DEFAULT 0 CHECK ("amount_base" IS NULL OR ("amount_base" BETWEEN -999999999999999999 AND 999999999999999999)),
    "method" TEXT NOT NULL DEFAULT 'bank' CHECK ("method" IS NULL OR (length("method")<=20)),
    "reference" TEXT CHECK ("reference" IS NULL OR (length("reference")<=100)),
    "notes" TEXT,
    "status" TEXT NOT NULL DEFAULT 'recorded' CHECK ("status" IS NULL OR (length("status")<=20)),
    "credit_note_id" TEXT CHECK ("credit_note_id" IS NULL OR (length("credit_note_id")=36)),
    "voided_at" TEXT,
    "created_by" TEXT CHECK ("created_by" IS NULL OR (length("created_by")=36)),
    "created_at" TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    "updated_at" TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    "id" TEXT NOT NULL CHECK ("id" IS NULL OR (length("id")=36)),
    "_revision" INTEGER NOT NULL DEFAULT 0 CHECK ("_revision" BETWEEN 0 AND 9007199254740991),
    PRIMARY KEY ("id"),
    UNIQUE ("org_id","number"),
    FOREIGN KEY ("party_id") REFERENCES "crm_contacts" ("id") ON DELETE RESTRICT
) STRICT;

CREATE TABLE "purchasing_purchase_order_lines" (
    "id" TEXT NOT NULL CHECK ("id" IS NULL OR (length("id")=36)),
    "order_id" TEXT NOT NULL CHECK ("order_id" IS NULL OR (length("order_id")=36)),
    "position" INTEGER NOT NULL DEFAULT 0,
    "product_id" TEXT CHECK ("product_id" IS NULL OR (length("product_id")=36)),
    "product_name" TEXT CHECK ("product_name" IS NULL OR (length("product_name")<=200)),
    "description" TEXT,
    "qty" INTEGER NOT NULL CHECK ("qty" IS NULL OR ("qty" BETWEEN -999999999999999999 AND 999999999999999999)),
    "uom_code" TEXT CHECK ("uom_code" IS NULL OR (length("uom_code")<=20)),
    "unit_price" INTEGER NOT NULL CHECK ("unit_price" IS NULL OR ("unit_price" BETWEEN -999999999999999999 AND 999999999999999999)),
    "discount_pct" INTEGER NOT NULL DEFAULT 0 CHECK ("discount_pct" IS NULL OR ("discount_pct" BETWEEN -99999 AND 99999)),
    "tax_id" TEXT CHECK ("tax_id" IS NULL OR (length("tax_id")=36)),
    "tax_rate_pct" INTEGER NOT NULL DEFAULT 0 CHECK ("tax_rate_pct" IS NULL OR ("tax_rate_pct" BETWEEN -99999 AND 99999)),
    "qty_received" INTEGER NOT NULL DEFAULT 0 CHECK ("qty_received" IS NULL OR ("qty_received" BETWEEN -999999999999999999 AND 999999999999999999)),
    "qty_invoiced" INTEGER NOT NULL DEFAULT 0 CHECK ("qty_invoiced" IS NULL OR ("qty_invoiced" BETWEEN -999999999999999999 AND 999999999999999999)),
    "line_subtotal" INTEGER NOT NULL DEFAULT 0 CHECK ("line_subtotal" IS NULL OR ("line_subtotal" BETWEEN -999999999999999999 AND 999999999999999999)),
    "line_tax" INTEGER NOT NULL DEFAULT 0 CHECK ("line_tax" IS NULL OR ("line_tax" BETWEEN -999999999999999999 AND 999999999999999999)),
    "line_total" INTEGER NOT NULL DEFAULT 0 CHECK ("line_total" IS NULL OR ("line_total" BETWEEN -999999999999999999 AND 999999999999999999)),
    "_revision" INTEGER NOT NULL DEFAULT 0 CHECK ("_revision" BETWEEN 0 AND 9007199254740991),
    PRIMARY KEY ("id"),
    FOREIGN KEY ("order_id") REFERENCES "purchasing_purchase_orders" ("id") ON DELETE CASCADE,
    FOREIGN KEY ("product_id") REFERENCES "catalog_products" ("id") ON DELETE SET NULL,
    FOREIGN KEY ("tax_id") REFERENCES "catalog_taxes" ("id") ON DELETE SET NULL
) STRICT;

CREATE TABLE "purchasing_purchase_orders" (
    "org_id" TEXT NOT NULL CHECK ("org_id" IS NULL OR (length("org_id")=36)),
    "branch_id" TEXT CHECK ("branch_id" IS NULL OR (length("branch_id")=36)),
    "number" TEXT NOT NULL CHECK ("number" IS NULL OR (length("number")<=30)),
    "supplier_id" TEXT CHECK ("supplier_id" IS NULL OR (length("supplier_id")=36)),
    "supplier_name" TEXT CHECK ("supplier_name" IS NULL OR (length("supplier_name")<=200)),
    "order_date" TEXT NOT NULL,
    "expected_date" TEXT,
    "currency" TEXT NOT NULL DEFAULT 'USD' CHECK ("currency" IS NULL OR (length("currency")<=3)),
    "status" TEXT NOT NULL DEFAULT 'draft' CHECK ("status" IS NULL OR (length("status")<=20)),
    "subtotal" INTEGER NOT NULL DEFAULT 0 CHECK ("subtotal" IS NULL OR ("subtotal" BETWEEN -999999999999999999 AND 999999999999999999)),
    "discount_total" INTEGER NOT NULL DEFAULT 0 CHECK ("discount_total" IS NULL OR ("discount_total" BETWEEN -999999999999999999 AND 999999999999999999)),
    "tax_total" INTEGER NOT NULL DEFAULT 0 CHECK ("tax_total" IS NULL OR ("tax_total" BETWEEN -999999999999999999 AND 999999999999999999)),
    "total" INTEGER NOT NULL DEFAULT 0 CHECK ("total" IS NULL OR ("total" BETWEEN -999999999999999999 AND 999999999999999999)),
    "fx_rate" INTEGER NOT NULL DEFAULT 100000000 CHECK ("fx_rate" IS NULL OR ("fx_rate" BETWEEN -999999999999999999 AND 999999999999999999)),
    "total_base" INTEGER NOT NULL DEFAULT 0 CHECK ("total_base" IS NULL OR ("total_base" BETWEEN -999999999999999999 AND 999999999999999999)),
    "notes" TEXT,
    "created_by" TEXT CHECK ("created_by" IS NULL OR (length("created_by")=36)),
    "confirmed_at" TEXT,
    "confirmed_by" TEXT CHECK ("confirmed_by" IS NULL OR (length("confirmed_by")=36)),
    "created_at" TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    "updated_at" TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    "id" TEXT NOT NULL CHECK ("id" IS NULL OR (length("id")=36)),
    "_revision" INTEGER NOT NULL DEFAULT 0 CHECK ("_revision" BETWEEN 0 AND 9007199254740991),
    PRIMARY KEY ("id"),
    UNIQUE ("org_id","number"),
    FOREIGN KEY ("branch_id") REFERENCES "core_branches" ("id") ON DELETE SET NULL,
    FOREIGN KEY ("supplier_id") REFERENCES "crm_contacts" ("id") ON DELETE SET NULL
) STRICT;

CREATE TABLE "sales_quotation_lines" (
    "id" TEXT NOT NULL CHECK ("id" IS NULL OR (length("id")=36)),
    "quotation_id" TEXT NOT NULL CHECK ("quotation_id" IS NULL OR (length("quotation_id")=36)),
    "position" INTEGER NOT NULL DEFAULT 0,
    "product_id" TEXT CHECK ("product_id" IS NULL OR (length("product_id")=36)),
    "product_name" TEXT CHECK ("product_name" IS NULL OR (length("product_name")<=200)),
    "description" TEXT,
    "qty" INTEGER NOT NULL CHECK ("qty" IS NULL OR ("qty" BETWEEN -999999999999999999 AND 999999999999999999)),
    "uom_code" TEXT CHECK ("uom_code" IS NULL OR (length("uom_code")<=20)),
    "unit_price" INTEGER NOT NULL CHECK ("unit_price" IS NULL OR ("unit_price" BETWEEN -999999999999999999 AND 999999999999999999)),
    "discount_pct" INTEGER NOT NULL DEFAULT 0 CHECK ("discount_pct" IS NULL OR ("discount_pct" BETWEEN -99999 AND 99999)),
    "tax_id" TEXT CHECK ("tax_id" IS NULL OR (length("tax_id")=36)),
    "tax_rate_pct" INTEGER NOT NULL DEFAULT 0 CHECK ("tax_rate_pct" IS NULL OR ("tax_rate_pct" BETWEEN -99999 AND 99999)),
    "line_subtotal" INTEGER NOT NULL DEFAULT 0 CHECK ("line_subtotal" IS NULL OR ("line_subtotal" BETWEEN -999999999999999999 AND 999999999999999999)),
    "line_tax" INTEGER NOT NULL DEFAULT 0 CHECK ("line_tax" IS NULL OR ("line_tax" BETWEEN -999999999999999999 AND 999999999999999999)),
    "line_total" INTEGER NOT NULL DEFAULT 0 CHECK ("line_total" IS NULL OR ("line_total" BETWEEN -999999999999999999 AND 999999999999999999)),
    "_revision" INTEGER NOT NULL DEFAULT 0 CHECK ("_revision" BETWEEN 0 AND 9007199254740991),
    PRIMARY KEY ("id"),
    FOREIGN KEY ("product_id") REFERENCES "catalog_products" ("id") ON DELETE SET NULL,
    FOREIGN KEY ("quotation_id") REFERENCES "sales_quotations" ("id") ON DELETE CASCADE,
    FOREIGN KEY ("tax_id") REFERENCES "catalog_taxes" ("id") ON DELETE SET NULL
) STRICT;

CREATE TABLE "sales_quotations" (
    "org_id" TEXT NOT NULL CHECK ("org_id" IS NULL OR (length("org_id")=36)),
    "number" TEXT NOT NULL CHECK ("number" IS NULL OR (length("number")<=30)),
    "customer_id" TEXT CHECK ("customer_id" IS NULL OR (length("customer_id")=36)),
    "customer_name" TEXT CHECK ("customer_name" IS NULL OR (length("customer_name")<=200)),
    "quote_date" TEXT NOT NULL,
    "valid_until" TEXT,
    "currency" TEXT NOT NULL DEFAULT 'USD' CHECK ("currency" IS NULL OR (length("currency")<=3)),
    "status" TEXT NOT NULL DEFAULT 'draft' CHECK ("status" IS NULL OR (length("status")<=20)),
    "subtotal" INTEGER NOT NULL DEFAULT 0 CHECK ("subtotal" IS NULL OR ("subtotal" BETWEEN -999999999999999999 AND 999999999999999999)),
    "discount_total" INTEGER NOT NULL DEFAULT 0 CHECK ("discount_total" IS NULL OR ("discount_total" BETWEEN -999999999999999999 AND 999999999999999999)),
    "tax_total" INTEGER NOT NULL DEFAULT 0 CHECK ("tax_total" IS NULL OR ("tax_total" BETWEEN -999999999999999999 AND 999999999999999999)),
    "total" INTEGER NOT NULL DEFAULT 0 CHECK ("total" IS NULL OR ("total" BETWEEN -999999999999999999 AND 999999999999999999)),
    "fx_rate" INTEGER NOT NULL DEFAULT 100000000 CHECK ("fx_rate" IS NULL OR ("fx_rate" BETWEEN -999999999999999999 AND 999999999999999999)),
    "total_base" INTEGER NOT NULL DEFAULT 0 CHECK ("total_base" IS NULL OR ("total_base" BETWEEN -999999999999999999 AND 999999999999999999)),
    "notes" TEXT,
    "created_by" TEXT CHECK ("created_by" IS NULL OR (length("created_by")=36)),
    "sent_at" TEXT,
    "accepted_at" TEXT,
    "converted_order_id" TEXT CHECK ("converted_order_id" IS NULL OR (length("converted_order_id")=36)),
    "created_at" TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    "updated_at" TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    "id" TEXT NOT NULL CHECK ("id" IS NULL OR (length("id")=36)),
    "_revision" INTEGER NOT NULL DEFAULT 0 CHECK ("_revision" BETWEEN 0 AND 9007199254740991),
    PRIMARY KEY ("id"),
    UNIQUE ("org_id","number"),
    FOREIGN KEY ("customer_id") REFERENCES "crm_contacts" ("id") ON DELETE SET NULL
) STRICT;

CREATE TABLE "sales_sales_order_lines" (
    "id" TEXT NOT NULL CHECK ("id" IS NULL OR (length("id")=36)),
    "order_id" TEXT NOT NULL CHECK ("order_id" IS NULL OR (length("order_id")=36)),
    "position" INTEGER NOT NULL DEFAULT 0,
    "product_id" TEXT CHECK ("product_id" IS NULL OR (length("product_id")=36)),
    "product_name" TEXT CHECK ("product_name" IS NULL OR (length("product_name")<=200)),
    "description" TEXT,
    "qty" INTEGER NOT NULL CHECK ("qty" IS NULL OR ("qty" BETWEEN -999999999999999999 AND 999999999999999999)),
    "uom_code" TEXT CHECK ("uom_code" IS NULL OR (length("uom_code")<=20)),
    "unit_price" INTEGER NOT NULL CHECK ("unit_price" IS NULL OR ("unit_price" BETWEEN -999999999999999999 AND 999999999999999999)),
    "discount_pct" INTEGER NOT NULL DEFAULT 0 CHECK ("discount_pct" IS NULL OR ("discount_pct" BETWEEN -99999 AND 99999)),
    "tax_id" TEXT CHECK ("tax_id" IS NULL OR (length("tax_id")=36)),
    "tax_rate_pct" INTEGER NOT NULL DEFAULT 0 CHECK ("tax_rate_pct" IS NULL OR ("tax_rate_pct" BETWEEN -99999 AND 99999)),
    "qty_delivered" INTEGER NOT NULL DEFAULT 0 CHECK ("qty_delivered" IS NULL OR ("qty_delivered" BETWEEN -999999999999999999 AND 999999999999999999)),
    "qty_invoiced" INTEGER NOT NULL DEFAULT 0 CHECK ("qty_invoiced" IS NULL OR ("qty_invoiced" BETWEEN -999999999999999999 AND 999999999999999999)),
    "line_subtotal" INTEGER NOT NULL DEFAULT 0 CHECK ("line_subtotal" IS NULL OR ("line_subtotal" BETWEEN -999999999999999999 AND 999999999999999999)),
    "line_tax" INTEGER NOT NULL DEFAULT 0 CHECK ("line_tax" IS NULL OR ("line_tax" BETWEEN -999999999999999999 AND 999999999999999999)),
    "line_total" INTEGER NOT NULL DEFAULT 0 CHECK ("line_total" IS NULL OR ("line_total" BETWEEN -999999999999999999 AND 999999999999999999)),
    "_revision" INTEGER NOT NULL DEFAULT 0 CHECK ("_revision" BETWEEN 0 AND 9007199254740991),
    PRIMARY KEY ("id"),
    FOREIGN KEY ("order_id") REFERENCES "sales_sales_orders" ("id") ON DELETE CASCADE,
    FOREIGN KEY ("product_id") REFERENCES "catalog_products" ("id") ON DELETE SET NULL,
    FOREIGN KEY ("tax_id") REFERENCES "catalog_taxes" ("id") ON DELETE SET NULL
) STRICT;

CREATE TABLE "sales_sales_orders" (
    "org_id" TEXT NOT NULL CHECK ("org_id" IS NULL OR (length("org_id")=36)),
    "branch_id" TEXT CHECK ("branch_id" IS NULL OR (length("branch_id")=36)),
    "number" TEXT NOT NULL CHECK ("number" IS NULL OR (length("number")<=30)),
    "customer_id" TEXT CHECK ("customer_id" IS NULL OR (length("customer_id")=36)),
    "customer_name" TEXT CHECK ("customer_name" IS NULL OR (length("customer_name")<=200)),
    "order_date" TEXT NOT NULL,
    "expected_date" TEXT,
    "currency" TEXT NOT NULL DEFAULT 'USD' CHECK ("currency" IS NULL OR (length("currency")<=3)),
    "status" TEXT NOT NULL DEFAULT 'draft' CHECK ("status" IS NULL OR (length("status")<=20)),
    "subtotal" INTEGER NOT NULL DEFAULT 0 CHECK ("subtotal" IS NULL OR ("subtotal" BETWEEN -999999999999999999 AND 999999999999999999)),
    "discount_total" INTEGER NOT NULL DEFAULT 0 CHECK ("discount_total" IS NULL OR ("discount_total" BETWEEN -999999999999999999 AND 999999999999999999)),
    "tax_total" INTEGER NOT NULL DEFAULT 0 CHECK ("tax_total" IS NULL OR ("tax_total" BETWEEN -999999999999999999 AND 999999999999999999)),
    "total" INTEGER NOT NULL DEFAULT 0 CHECK ("total" IS NULL OR ("total" BETWEEN -999999999999999999 AND 999999999999999999)),
    "fx_rate" INTEGER NOT NULL DEFAULT 100000000 CHECK ("fx_rate" IS NULL OR ("fx_rate" BETWEEN -999999999999999999 AND 999999999999999999)),
    "total_base" INTEGER NOT NULL DEFAULT 0 CHECK ("total_base" IS NULL OR ("total_base" BETWEEN -999999999999999999 AND 999999999999999999)),
    "notes" TEXT,
    "created_by" TEXT CHECK ("created_by" IS NULL OR (length("created_by")=36)),
    "confirmed_at" TEXT,
    "confirmed_by" TEXT CHECK ("confirmed_by" IS NULL OR (length("confirmed_by")=36)),
    "created_at" TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    "updated_at" TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    "id" TEXT NOT NULL CHECK ("id" IS NULL OR (length("id")=36)),
    "_revision" INTEGER NOT NULL DEFAULT 0 CHECK ("_revision" BETWEEN 0 AND 9007199254740991),
    PRIMARY KEY ("id"),
    UNIQUE ("org_id","number"),
    FOREIGN KEY ("branch_id") REFERENCES "core_branches" ("id") ON DELETE SET NULL,
    FOREIGN KEY ("customer_id") REFERENCES "crm_contacts" ("id") ON DELETE SET NULL
) STRICT;

CREATE INDEX "ix_accounting_accounts_org_id" ON "accounting_accounts" ("org_id");

CREATE INDEX "ix_accounting_journal_entries_org_id" ON "accounting_journal_entries" ("org_id");

CREATE INDEX "ix_accounting_journal_entries_source_id" ON "accounting_journal_entries" ("source_id");

CREATE INDEX "ix_accounting_journal_entries_source_type" ON "accounting_journal_entries" ("source_type");

CREATE INDEX "ix_accounting_journal_lines_account_id" ON "accounting_journal_lines" ("account_id");

CREATE INDEX "ix_accounting_journal_lines_entry_id" ON "accounting_journal_lines" ("entry_id");

CREATE INDEX "ix_catalog_product_categories_org_id" ON "catalog_product_categories" ("org_id");

CREATE INDEX "ix_catalog_products_org_id" ON "catalog_products" ("org_id");

CREATE INDEX "ix_catalog_products_sku" ON "catalog_products" ("sku");

CREATE INDEX "ix_catalog_taxes_org_id" ON "catalog_taxes" ("org_id");

CREATE INDEX "ix_catalog_uoms_org_id" ON "catalog_uoms" ("org_id");

CREATE INDEX "ix_core_ai_drafts_entity_id" ON "core_ai_drafts" ("entity_id");

CREATE INDEX "ix_core_ai_drafts_entity_type" ON "core_ai_drafts" ("entity_type");

CREATE INDEX "ix_core_ai_drafts_org_id" ON "core_ai_drafts" ("org_id");

CREATE INDEX "ix_core_ai_drafts_status" ON "core_ai_drafts" ("status");

CREATE INDEX "ix_core_audit_logs_action" ON "core_audit_logs" ("action");

CREATE INDEX "ix_core_audit_logs_created_at" ON "core_audit_logs" ("created_at");

CREATE INDEX "ix_core_audit_logs_entity_type" ON "core_audit_logs" ("entity_type");

CREATE INDEX "ix_core_audit_logs_org_id" ON "core_audit_logs" ("org_id");

CREATE INDEX "ix_core_audit_logs_user_id" ON "core_audit_logs" ("user_id");

CREATE INDEX "ix_core_currencies_org_id" ON "core_currencies" ("org_id");

CREATE INDEX "ix_core_document_sequences_org_id" ON "core_document_sequences" ("org_id");

CREATE INDEX "ix_core_fx_rates_currency" ON "core_fx_rates" ("currency");

CREATE INDEX "ix_core_fx_rates_org_id" ON "core_fx_rates" ("org_id");

CREATE INDEX "ix_core_fx_rates_rate_date" ON "core_fx_rates" ("rate_date");

CREATE INDEX "ix_core_notifications_user_id" ON "core_notifications" ("user_id");

CREATE UNIQUE INDEX "ix_core_permissions_code" ON "core_permissions" ("code");

CREATE INDEX "ix_core_permissions_module" ON "core_permissions" ("module");

CREATE INDEX "ix_core_settings_org_id" ON "core_settings" ("org_id");

CREATE UNIQUE INDEX "ix_core_users_email" ON "core_users" ("email");

CREATE INDEX "ix_crm_contacts_code" ON "crm_contacts" ("code");

CREATE INDEX "ix_crm_contacts_org_id" ON "crm_contacts" ("org_id");

CREATE INDEX "ix_hr_departments_org_id" ON "hr_departments" ("org_id");

CREATE INDEX "ix_hr_employees_number" ON "hr_employees" ("number");

CREATE INDEX "ix_hr_employees_org_id" ON "hr_employees" ("org_id");

CREATE INDEX "ix_hr_leave_requests_employee_id" ON "hr_leave_requests" ("employee_id");

CREATE INDEX "ix_hr_leave_requests_org_id" ON "hr_leave_requests" ("org_id");

CREATE INDEX "ix_hr_leave_requests_status" ON "hr_leave_requests" ("status");

CREATE INDEX "ix_hr_leave_types_org_id" ON "hr_leave_types" ("org_id");

CREATE INDEX "ix_inventory_adjustment_lines_adjustment_id" ON "inventory_adjustment_lines" ("adjustment_id");

CREATE INDEX "ix_inventory_adjustment_lines_product_id" ON "inventory_adjustment_lines" ("product_id");

CREATE INDEX "ix_inventory_adjustments_org_id" ON "inventory_adjustments" ("org_id");

CREATE INDEX "ix_inventory_adjustments_status" ON "inventory_adjustments" ("status");

CREATE INDEX "ix_inventory_deliveries_org_id" ON "inventory_deliveries" ("org_id");

CREATE INDEX "ix_inventory_deliveries_status" ON "inventory_deliveries" ("status");

CREATE INDEX "ix_inventory_delivery_lines_delivery_id" ON "inventory_delivery_lines" ("delivery_id");

CREATE INDEX "ix_inventory_delivery_lines_product_id" ON "inventory_delivery_lines" ("product_id");

CREATE INDEX "ix_inventory_receipt_lines_product_id" ON "inventory_receipt_lines" ("product_id");

CREATE INDEX "ix_inventory_receipt_lines_receipt_id" ON "inventory_receipt_lines" ("receipt_id");

CREATE INDEX "ix_inventory_receipts_org_id" ON "inventory_receipts" ("org_id");

CREATE INDEX "ix_inventory_receipts_status" ON "inventory_receipts" ("status");

CREATE INDEX "ix_inventory_stock_org_id" ON "inventory_stock" ("org_id");

CREATE INDEX "ix_inventory_stock_product_id" ON "inventory_stock" ("product_id");

CREATE INDEX "ix_inventory_stock_warehouse_id" ON "inventory_stock" ("warehouse_id");

CREATE INDEX "ix_inventory_stock_moves_move_type" ON "inventory_stock_moves" ("move_type");

CREATE INDEX "ix_inventory_stock_moves_org_id" ON "inventory_stock_moves" ("org_id");

CREATE INDEX "ix_inventory_stock_moves_product_id" ON "inventory_stock_moves" ("product_id");

CREATE INDEX "ix_inventory_stock_moves_reverses_move_id" ON "inventory_stock_moves" ("reverses_move_id");

CREATE INDEX "ix_inventory_stock_moves_warehouse_id" ON "inventory_stock_moves" ("warehouse_id");

CREATE INDEX "ix_inventory_warehouses_org_id" ON "inventory_warehouses" ("org_id");

CREATE INDEX "ix_invoicing_invoice_lines_invoice_id" ON "invoicing_invoice_lines" ("invoice_id");

CREATE INDEX "ix_invoicing_invoices_invoice_type" ON "invoicing_invoices" ("invoice_type");

CREATE INDEX "ix_invoicing_invoices_org_id" ON "invoicing_invoices" ("org_id");

CREATE INDEX "ix_invoicing_invoices_original_invoice_id" ON "invoicing_invoices" ("original_invoice_id");

CREATE INDEX "ix_invoicing_invoices_status" ON "invoicing_invoices" ("status");

CREATE INDEX "ix_invoicing_payment_allocations_invoice_id" ON "invoicing_payment_allocations" ("invoice_id");

CREATE INDEX "ix_invoicing_payment_allocations_payment_id" ON "invoicing_payment_allocations" ("payment_id");

CREATE INDEX "ix_invoicing_payments_credit_note_id" ON "invoicing_payments" ("credit_note_id");

CREATE INDEX "ix_invoicing_payments_org_id" ON "invoicing_payments" ("org_id");

CREATE INDEX "ix_invoicing_payments_status" ON "invoicing_payments" ("status");

CREATE INDEX "ix_purchasing_purchase_order_lines_order_id" ON "purchasing_purchase_order_lines" ("order_id");

CREATE INDEX "ix_purchasing_purchase_orders_number" ON "purchasing_purchase_orders" ("number");

CREATE INDEX "ix_purchasing_purchase_orders_org_id" ON "purchasing_purchase_orders" ("org_id");

CREATE INDEX "ix_purchasing_purchase_orders_status" ON "purchasing_purchase_orders" ("status");

CREATE INDEX "ix_sales_quotation_lines_quotation_id" ON "sales_quotation_lines" ("quotation_id");

CREATE INDEX "ix_sales_quotations_number" ON "sales_quotations" ("number");

CREATE INDEX "ix_sales_quotations_org_id" ON "sales_quotations" ("org_id");

CREATE INDEX "ix_sales_quotations_status" ON "sales_quotations" ("status");

CREATE INDEX "ix_sales_sales_order_lines_order_id" ON "sales_sales_order_lines" ("order_id");

CREATE INDEX "ix_sales_sales_orders_number" ON "sales_sales_orders" ("number");

CREATE INDEX "ix_sales_sales_orders_org_id" ON "sales_sales_orders" ("org_id");

CREATE INDEX "ix_sales_sales_orders_status" ON "sales_sales_orders" ("status");
