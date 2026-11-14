export type SectionTab = {
  to: string
  label: string
  end?: boolean
  permission: string | string[]
}

export const PRODUCT_TABS: SectionTab[] = [
  {
    to: "/products",
    label: "Products",
    end: true,
    permission: "catalog.product.read",
  },
  {
    to: "/products/ai-drafts",
    label: "AI drafts",
    permission: "catalog.product.update",
  },
]
export const SALES_TABS: SectionTab[] = [
  { to: "/sales", label: "Orders", end: true, permission: "sales.order.read" },
  {
    to: "/sales/quotations",
    label: "Quotations",
    permission: "sales.quote.read",
  },
]
export const INVENTORY_TABS: SectionTab[] = [
  {
    to: "/inventory",
    label: "Stock",
    end: true,
    permission: "inventory.stock.read",
  },
  {
    to: "/inventory/receipts",
    label: "Receipts",
    permission: "inventory.receipt.read",
  },
  {
    to: "/inventory/deliveries",
    label: "Deliveries",
    permission: "inventory.delivery.read",
  },
  {
    to: "/inventory/adjustments",
    label: "Adjustments",
    permission: "inventory.adjustment.read",
  },
]
export const INVOICING_TABS: SectionTab[] = [
  {
    to: "/invoicing/ar",
    label: "Customer invoices",
    permission: "invoicing.invoice.read",
  },
  {
    to: "/invoicing/ap",
    label: "Supplier bills",
    permission: "invoicing.invoice.read",
  },
  {
    to: "/invoicing/payments",
    label: "Payments",
    permission: "invoicing.payment.read",
  },
]
export const ACCOUNTING_TABS: SectionTab[] = [
  {
    to: "/accounting/accounts",
    label: "Chart of accounts",
    permission: "accounting.account.read",
  },
  {
    to: "/accounting/journal",
    label: "Journal entries",
    permission: "accounting.entry.read",
  },
  {
    to: "/accounting/trial-balance",
    label: "Trial balance",
    permission: "accounting.entry.read",
  },
]
export const HR_TABS: SectionTab[] = [
  { to: "/hr", label: "Employees", end: true, permission: "hr.employee.read" },
  { to: "/hr/leave", label: "Leave requests", permission: "hr.leave.request" },
]
export const SETTINGS_TABS: SectionTab[] = [
  { to: "/settings", label: "Users", end: true, permission: "core.user.read" },
  { to: "/settings/roles", label: "Roles", permission: "core.role.read" },
  {
    to: "/settings/organization",
    label: "Organization",
    permission: "core.org.read",
  },
  {
    to: "/settings/catalog",
    label: "Catalog",
    permission: [
      "catalog.tax.read",
      "catalog.uom.read",
      "catalog.category.read",
    ],
  },
  {
    to: "/settings/currencies",
    label: "Currencies",
    permission: "core.settings.read",
  },
  {
    to: "/settings/numbering",
    label: "Numbering",
    permission: "core.settings.read",
  },
  { to: "/settings/audit", label: "Audit log", permission: "core.audit.read" },
]
