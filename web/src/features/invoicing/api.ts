/** Invoicing API. */

import { api } from "@/lib/api/client"
import type { Contact } from "@/features/contacts/api"
import type { Page } from "@/features/settings/api"
import type { Order } from "@/features/documents/api"

export type Invoice = {
  id: string
  invoice_type: "ar" | "ap"
  number: string | null
  party_id: string | null
  party_name: string | null
  source_type: string | null
  source_id: string | null
  source_number: string | null
  invoice_date: string
  due_date: string | null
  currency: string
  status: string
  subtotal: string
  discount_total: string
  tax_total: string
  total: string
  amount_paid: string
  lines: {
    id: string
    product_name: string | null
    description: string | null
    qty: string
    unit_price: string
    line_total: string
  }[]
}

export type Payment = {
  id: string
  number: string
  direction: string
  party_id: string
  party_name: string | null
  payment_date: string
  amount: string
  method: string
  reference: string | null
  status: string
  allocations: { id: string; invoice_id: string; amount: string }[]
}

export type Statement = {
  party_id: string
  party_name: string | null
  open_balance: string
  invoices: {
    invoice_id: string
    number: string | null
    invoice_date: string
    due_date: string | null
    total: string
    amount_paid: string
    balance: string
    status: string
  }[]
}

export const invoicingApi = {
  invoices: (params: { invoice_type?: string; status?: string; limit?: number } = {}) => {
    const clean = Object.fromEntries(
      Object.entries(params).filter(([, v]) => v !== undefined && v !== "") as [string, string][],
    )
    return api.get<Page<Invoice>>(`/api/invoicing/invoices?${new URLSearchParams(clean)}`)
  },
  createInvoice: (body: {
    invoice_type: "ar" | "ap"
    party_id: string
    source_order_id?: string
    lines?: { product_id?: string | null; description?: string; qty: string; unit_price?: string }[]
  }) => api.post<Invoice>("/api/invoicing/invoices", body),
  postInvoice: (id: string) => api.post<Invoice>(`/api/invoicing/invoices/${id}/post`, undefined),
  voidInvoice: (id: string) => api.post<Invoice>(`/api/invoicing/invoices/${id}/void`, undefined),

  payments: (params: { direction?: string; limit?: number } = {}) => {
    const clean = Object.fromEntries(
      Object.entries(params).filter(([, v]) => v !== undefined && v !== "") as [string, string][],
    )
    return api.get<Page<Payment>>(`/api/invoicing/payments?${new URLSearchParams(clean)}`)
  },
  recordPayment: (body: {
    direction: "in" | "out"
    party_id: string
    amount: string
    method: string
    allocations: { invoice_id: string; amount: string }[]
  }) => api.post<Payment>("/api/invoicing/payments", body),
  voidPayment: (id: string) => api.post<Payment>(`/api/invoicing/payments/${id}/void`, undefined),

  statement: (partyId: string) => api.get<Statement>(`/api/invoicing/statement/${partyId}`),
  contacts: () => api.get<Page<Contact>>("/api/crm/contacts?limit=100"),
  salesOrders: (status: string) => api.get<Page<Order>>(`/api/sales/orders?status=${status}&limit=100`),
  purchaseOrders: (status: string) =>
    api.get<Page<Order>>(`/api/purchasing/orders?status=${status}&limit=100`),
}
