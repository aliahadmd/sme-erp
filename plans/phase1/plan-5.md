# Plan 5 — Master Data: Contacts (CRM), Products, UoM, Taxes

- **Depends on:** plan-4
- **Goal:** everything you reference on documents, done well once: parties
  (customers/suppliers) and the product catalog with pricing/UoM/tax support.

## 1. Contacts — module `crm` (schema `crm`)

- [ ] `contacts` — **unified party table** (see index decision 2):
      `is_customer`, `is_supplier` flags; kind (company|person); name (+ company
      registration name); auto code (`C-0001` via numbering service); tax_id;
      emails/phones/socials as JSONB array of typed entries; addresses JSONB
      (billing/shipping labeled); currency (defaults org base); payment_terms_days
      (default from settings); credit_limit NUMERIC; tags text[]; owner_user_id;
      notes; status (active/archived); UUIDv7 pk; TimestampMixin.
- [ ] Endpoints: CRUD + `GET /api/crm/contacts?type=customer|supplier&q=&tag=&page=`
      (paginated, searchable); permissions `crm.contact.read|create|update|delete`;
      archive instead of delete when referenced by documents; audit on every mutation.
- [ ] Frontend `features/contacts`: list page (tabs All / Customers / Suppliers,
      column search, tag filter, pagination), form dialog/page with
      flags + addresses + terms, detail drawer with recent documents (wired as those
      modules land), bulk archive.

## 2. Product catalog — module `catalog` (schema `catalog`)

- [ ] `uoms` — code ( pcs, kg, hr…), name. Seeded basics.
- [ ] `taxes` — code, name, rate_pct NUMERIC(5,2), applies_to (sale|purchase|both),
      is_default flags for sale/purchase. Seeded with one 0% "No tax".
- [ ] `product_categories` — parent_id tree, name.
- [ ] `products` — sku (unique per org), barcode, name, description, type
      (goods|service), category_id, uom_id, is_purchasable, is_sellable,
      track_inventory (bool; forced false for services), standard
      `sale_price` / `cost_price` NUMERIC(18,6) (editable per document line),
      default sale tax / purchase tax FKs, min_stock NUMERIC(18,4) (low-stock report),
      image_key (S3 object key — upload UI is phase 2, column ready), status
      (active/archived).
- [ ] Endpoints: CRUD + search/paginate for products, categories, taxes, uoms;
      permissions `catalog.product.*`, `catalog.tax.*`, …; audit everywhere.
      SKU auto-suggest from name when left blank.
- [ ] Frontend `features/products`: list with category/type filters, product form
      (tabbed: General / Pricing & Tax / Inventory), category tree manager, and
      **Settings → Taxes / Units** admin pages.

## 3. Cross-cutting

- [ ] Numbering service from `shared/` used for contact codes here (reused by all
      documents in plan-6+): per org + entity + year, `SELECT … FOR UPDATE` sequence,
      prefix from settings (e.g. `C-2026-0001`).
- [ ] Shared frontend building blocks extracted once, reused by later plans:
      `<DataTable>` (TanStack Table wrapper: sort/filter/paginate), `<FormDialog>`,
      `<MoneyInput>`, `<PartyPicker>` (command palette search), `<ProductPicker>`.
- [ ] Tests: contact CRUD + search, product validation (service cannot track inventory),
      numbering sequence correctness under concurrency.

## Acceptance

- [ ] A party can be both customer and supplier and appears in both pickers.
- [ ] Full CRUD + search works for contacts and products; archive hides from pickers
      but keeps history.
- [ ] Referential safety: cannot archive a tax/uom in use; contact with documents
      archives instead of deleting.
- [ ] `make verify` green.
