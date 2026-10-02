/** Inventory API. */

import { api } from "@/lib/api/client"
import type { Page } from "@/features/settings/api"
import type { Product } from "@/features/products/api"
import type { Order } from "@/features/documents/api"

export type Warehouse = { id: string; code: string; name: string; is_default: boolean }

export type StockRow = {
  product_id: string
  product_sku: string
  product_name: string
  warehouse_id: string
  warehouse_code: string
  qty_on_hand: string
  avg_cost: string
  stock_value: string
  is_low: boolean
}

export type StockMove = {
  id: string
  product_id: string
  warehouse_id: string
  qty: string
  move_type: string
  ref_type: string | null
  ref_number: string | null
  unit_cost: string
  cogs: string
  moved_at: string
}

export type Receipt = {
  id: string
  number: string
  warehouse_id: string
  source_type: string | null
  source_id: string | null
  source_number: string | null
  status: string
  notes: string | null
  posted_at: string | null
  lines: { id: string; product_id: string; qty: string; unit_cost: string }[]
}

export type Delivery = {
  id: string
  number: string
  warehouse_id: string
  source_type: string | null
  source_id: string | null
  source_number: string | null
  status: string
  notes: string | null
  posted_at: string | null
  lines: { id: string; product_id: string; qty: string }[]
}

export type Adjustment = {
  id: string
  number: string
  warehouse_id: string
  reason: string
  notes: string | null
  status: string
  posted_at: string | null
  lines: { id: string; product_id: string; qty: string }[]
}

export type { Product }

export const inventoryApi = {
  warehouses: () => api.get<Warehouse[]>("/api/inventory/warehouses"),
  createWarehouse: (body: { code: string; name: string; is_default?: boolean }) =>
    api.post<Warehouse>("/api/inventory/warehouses", body),
  stock: (params: { warehouse_id?: string; low_only?: boolean } = {}) => {
    const clean = Object.fromEntries(
      Object.entries(params).filter(([, v]) => v !== undefined && v !== false && v !== "") as [string, string][],
    )
    return api.get<StockRow[]>(`/api/inventory/stock?${new URLSearchParams(clean)}`)
  },
  moves: (params: { product_id?: string; limit?: number } = {}) => {
    const clean = Object.fromEntries(
      Object.entries(params)
        .filter(([, v]) => v !== undefined && v !== "")
        .map(([k, v]) => [k, String(v)]),
    )
    return api.get<Page<StockMove>>(`/api/inventory/moves?${new URLSearchParams(clean)}`)
  },

  receipts: (params: { status?: string; limit?: number } = {}) => {
    const clean = Object.fromEntries(
      Object.entries(params)
        .filter(([, v]) => v !== undefined && v !== "")
        .map(([k, v]) => [k, String(v)]),
    )
    return api.get<Page<Receipt>>(`/api/inventory/receipts?${new URLSearchParams(clean)}`)
  },
  createReceipt: (body: { source_po_id?: string; notes?: string; lines?: { product_id: string; qty: string; unit_cost: string }[] }) =>
    api.post<Receipt>("/api/inventory/receipts", body),
  postReceipt: (id: string) => api.post<Receipt>(`/api/inventory/receipts/${id}/post`, undefined),
  voidReceipt: (id: string) => api.post<Receipt>(`/api/inventory/receipts/${id}/void`, undefined),

  deliveries: (params: { status?: string; limit?: number } = {}) => {
    const clean = Object.fromEntries(
      Object.entries(params)
        .filter(([, v]) => v !== undefined && v !== "")
        .map(([k, v]) => [k, String(v)]),
    )
    return api.get<Page<Delivery>>(`/api/inventory/deliveries?${new URLSearchParams(clean)}`)
  },
  createDelivery: (body: { source_so_id?: string; notes?: string; lines?: { product_id: string; qty: string }[] }) =>
    api.post<Delivery>("/api/inventory/deliveries", body),
  postDelivery: (id: string) => api.post<Delivery>(`/api/inventory/deliveries/${id}/post`, undefined),
  voidDelivery: (id: string) => api.post<Delivery>(`/api/inventory/deliveries/${id}/void`, undefined),

  adjustments: (params: { limit?: number } = {}) => {
    const clean = new URLSearchParams()
    if (params.limit) clean.set("limit", String(params.limit))
    return api.get<Page<Adjustment>>(`/api/inventory/adjustments?${clean}`)
  },
  createAdjustment: (body: {
    reason: string
    notes?: string
    lines: { product_id: string; qty: string }[]
  }) => api.post<Adjustment>("/api/inventory/adjustments", body),
  postAdjustment: (id: string) => api.post<Adjustment>(`/api/inventory/adjustments/${id}/post`, undefined),

  // All orders (status filtered client-side): partly received/delivered or
  // already-invoiced orders can still have goods outstanding.
  purchaseOrders: () => api.get<Page<Order>>("/api/purchasing/orders?limit=100"),
  salesOrders: () => api.get<Page<Order>>("/api/sales/orders?limit=100"),
  products: () => api.get<Page<Product>>("/api/catalog/products?limit=100"),
}
