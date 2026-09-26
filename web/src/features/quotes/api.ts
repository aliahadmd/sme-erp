/** Quotations API. */

import { api } from "@/lib/api/client"
import type { Contact } from "@/features/contacts/api"
import type { Page } from "@/features/settings/api"
import type { Product } from "@/features/products/api"

export type QuoteStatus =
  | "draft"
  | "sent"
  | "accepted"
  | "converted"
  | "rejected"
  | "cancelled"
  | "expired"

export type Quotation = {
  id: string
  number: string
  customer_id: string | null
  customer_name: string | null
  quote_date: string
  valid_until: string | null
  currency: string
  status: QuoteStatus
  subtotal: string
  discount_total: string
  tax_total: string
  total: string
  notes: string | null
  lines: {
    id: string
    product_id: string | null
    product_name: string | null
    description: string | null
    qty: string
    unit_price: string
    line_total: string
  }[]
}

export type QuoteLineInput = {
  product_id: string | null
  description?: string | null
  qty: string
  unit_price?: string | null
  discount_pct?: string
  tax_id?: string | null
}

export const quotesApi = {
  list: (params: { status?: string; q?: string; limit?: number } = {}) => {
    const clean = Object.fromEntries(
      Object.entries(params).filter(([, v]) => v !== undefined && v !== "") as [string, string][],
    )
    return api.get<Page<Quotation>>(`/api/sales/quotations?${new URLSearchParams(clean)}`)
  },
  get: (id: string) => api.get<Quotation>(`/api/sales/quotations/${id}`),
  create: (body: {
    customer_id: string
    valid_until?: string | null
    notes?: string | null
    lines: QuoteLineInput[]
  }) => api.post<Quotation>("/api/sales/quotations", body),
  update: (id: string, body: Record<string, unknown>) =>
    api.patch<Quotation>(`/api/sales/quotations/${id}`, body),
  send: (id: string) => api.post<Quotation>(`/api/sales/quotations/${id}/send`, undefined),
  accept: (id: string) => api.post<Quotation>(`/api/sales/quotations/${id}/accept`, undefined),
  reject: (id: string) => api.post<Quotation>(`/api/sales/quotations/${id}/reject`, undefined),
  cancel: (id: string) => api.post<Quotation>(`/api/sales/quotations/${id}/cancel`, undefined),
  convert: (id: string) =>
    api.post<{ order_id: string; order_number: string }>(
      `/api/sales/quotations/${id}/convert`,
      undefined,
    ),
  contacts: () => api.get<Page<Contact>>("/api/crm/contacts?limit=100"),
  products: () => api.get<Page<Product>>("/api/catalog/products?limit=100"),
}
