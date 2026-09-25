/** Order document API — shared by sales and purchasing. */

import { api } from "@/lib/api/client"
import type { Contact } from "@/features/contacts/api"
import type { Product, Tax, Uom } from "@/features/products/api"
import type { Page } from "@/features/settings/api"

export type OrderLine = {
  id?: string
  product_id: string | null
  description?: string | null
  qty: string
  unit_price: string | null
  discount_pct: string
  tax_id: string | null
  // read-only server fields
  product_name?: string | null
  uom_code?: string | null
  tax_rate_pct?: string
  line_subtotal?: string
  line_tax?: string
  line_total?: string
  position?: number
}

export type Order = {
  id: string
  number: string
  customer_id?: string | null
  customer_name?: string | null
  supplier_id?: string | null
  supplier_name?: string | null
  order_date: string
  expected_date?: string | null
  currency: string
  status: string
  subtotal: string
  discount_total: string
  tax_total: string
  total: string
  notes?: string | null
  lines: OrderLine[]
}

export type OrderModule = "sales" | "purchasing"

const partyField: Record<OrderModule, string> = {
  sales: "customer_id",
  purchasing: "supplier_id",
}

export const partyLabel: Record<OrderModule, string> = {
  sales: "Customer",
  purchasing: "Supplier",
}

function base(module: OrderModule) {
  return module === "sales" ? "/api/sales/orders" : "/api/purchasing/orders"
}

export const ordersApi = {
  list: (
    module: OrderModule,
    params: { status?: string; q?: string; limit?: number; offset?: number } = {},
  ) => {
    const clean = Object.fromEntries(
      Object.entries(params).filter(([, v]) => v !== undefined && v !== "") as [string, string][],
    )
    return api.get<Page<Order>>(`${base(module)}?${new URLSearchParams(clean)}`)
  },
  get: (module: OrderModule, id: string) => api.get<Order>(`${base(module)}/${id}`),
  create: (module: OrderModule, body: Record<string, unknown>) =>
    api.post<Order>(base(module), body),
  update: (module: OrderModule, id: string, body: Record<string, unknown>) =>
    api.patch<Order>(`${base(module)}/${id}`, body),
  confirm: (module: OrderModule, id: string) =>
    api.post<Order>(`${base(module)}/${id}/confirm`, undefined),
  cancel: (module: OrderModule, id: string) =>
    api.post<Order>(`${base(module)}/${id}/cancel`, undefined),

  contacts: () => api.get<Page<Contact>>("/api/crm/contacts?limit=100"),
  products: () => api.get<Page<Product>>("/api/catalog/products?limit=100"),
  taxes: () => api.get<Tax[]>("/api/catalog/taxes"),
  uoms: () => api.get<Uom[]>("/api/catalog/uoms"),
}

export { partyField }
