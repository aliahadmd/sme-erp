/**
 * Centralized TanStack Query keys.
 * Convention: [scope, ...identifiers, params]
 */
export const queryKeys = {
  me: () => ["auth", "me"] as const,
  contacts: (params: Record<string, unknown> = {}) => ["crm", "contacts", params] as const,
  contact: (id: string) => ["crm", "contacts", id] as const,
  products: (params: Record<string, unknown> = {}) => ["catalog", "products", params] as const,
  product: (id: string) => ["catalog", "products", id] as const,
  salesOrders: (params: Record<string, unknown> = {}) => ["sales", "orders", params] as const,
  salesOrder: (id: string) => ["sales", "orders", id] as const,
  purchaseOrders: (params: Record<string, unknown> = {}) => ["purchasing", "orders", params] as const,
  purchaseOrder: (id: string) => ["purchasing", "orders", id] as const,
  stock: (params: Record<string, unknown> = {}) => ["inventory", "stock", params] as const,
  invoices: (params: Record<string, unknown> = {}) => ["invoicing", "invoices", params] as const,
  invoice: (id: string) => ["invoicing", "invoices", id] as const,
  payments: (params: Record<string, unknown> = {}) => ["invoicing", "payments", params] as const,
  auditLogs: (params: Record<string, unknown> = {}) => ["core", "audit-logs", params] as const,
  notifications: () => ["core", "notifications"] as const,
}
