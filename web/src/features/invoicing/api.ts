/** Invoicing API. */

import { api } from "@/lib/api/client"
import type { Contact } from "@/features/contacts/api"
import type { Page } from "@/features/settings/api"
import type { Order } from "@/features/documents/api"

export type InvoiceType = "ar" | "ap" | "ar_credit" | "ap_credit"

export type Invoice = {
  id: string
  invoice_type: InvoiceType
  original_invoice_id: string | null
  number: string | null
  party_id: string | null
  party_name: string | null
  source_type: string | null
  source_id: string | null
  source_number: string | null
  invoice_date: string
  due_date: string | null
  currency: string
  fx_rate: string
  status: string
  subtotal: string
  discount_total: string
  tax_total: string
  total: string
  total_base: string
  amount_paid: string
  applied_credits: string
  /** total − paid − applied credits (credit notes: amount still refundable) */
  open_balance: string
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
  currency: string
  amount: string
  method: string
  reference: string | null
  status: string
  credit_note_id: string | null
  unallocated: string
  allocations: { id: string; invoice_id: string; amount: string }[]
}

export type Statement = {
  party_id: string
  party_name: string | null
  open_balance: string
  unapplied_payments: string
  invoices: {
    invoice_id: string
    invoice_type: InvoiceType
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
  invoices: (
    params: { side?: "ar" | "ap"; invoice_type?: string; status?: string; limit?: number } = {},
  ) => {
    const clean = Object.fromEntries(
      Object.entries(params).filter(([, v]) => v !== undefined && v !== "") as [string, string][],
    )
    return api.get<Page<Invoice>>(`/api/invoicing/invoices?${new URLSearchParams(clean)}`)
  },
  createInvoice: (body: {
    invoice_type: InvoiceType
    party_id: string
    source_order_id?: string
    original_invoice_id?: string
    currency?: string
    lines?: { product_id?: string | null; description?: string; qty: string; unit_price?: string }[]
  }) => api.post<Invoice>("/api/invoicing/invoices", body),
  postInvoice: (id: string) => api.post<Invoice>(`/api/invoicing/invoices/${id}/post`, undefined),
  voidInvoice: (id: string) => api.post<Invoice>(`/api/invoicing/invoices/${id}/void`, undefined),
  deleteDraft: (id: string) => api.delete<void>(`/api/invoicing/invoices/${id}`),
  emailInvoice: (id: string) =>
    api.post<{ status: string; to: string }>(`/api/invoicing/invoices/${id}/send-email`, undefined),

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
    credit_note_id?: string
    allocations: { invoice_id: string; amount: string }[]
  }) => api.post<Payment>("/api/invoicing/payments", body),
  allocatePayment: (id: string, allocations: { invoice_id: string; amount: string }[]) =>
    api.post<Payment>(`/api/invoicing/payments/${id}/allocate`, { allocations }),
  voidPayment: (id: string) => api.post<Payment>(`/api/invoicing/payments/${id}/void`, undefined),

  statement: (partyId: string) => api.get<Statement>(`/api/invoicing/statement/${partyId}`),
  contacts: () => api.get<Page<Contact>>("/api/crm/contacts?limit=100"),
  salesOrders: () => api.get<Page<Order>>("/api/sales/orders?limit=100"),
  purchaseOrders: () => api.get<Page<Order>>("/api/purchasing/orders?limit=100"),
}
