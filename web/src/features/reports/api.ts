/** Dashboard + reports API. */

import { api } from "@/lib/api/client"

export type Dashboard = {
  sales_mtd: string
  purchases_mtd: string
  open_ar: string
  open_ap: string
  low_stock_count: number
  weeks: { week_start: string; sales: string; purchases: string }[]
}

export type ByCustomerRow = {
  party_id: string | null
  party_name: string
  invoice_count: number
  total: string
}

export type AgingRow = { bucket: string; amount: string }

export type StockValuationRow = {
  warehouse_code: string
  warehouse_name: string
  products: number
  qty_on_hand: string
  value: string
}

export type TaxSummaryRow = {
  tax_code: string | null
  tax_name: string | null
  rate_pct: string | null
  net: string
  tax: string
}

export type Notification = {
  id: string
  type: string
  title: string
  body: string | null
  link: string | null
  read_at: string | null
  created_at: string
}

const qs = (params: Record<string, string | undefined>) => {
  const clean = Object.fromEntries(
    Object.entries(params).filter(([, v]) => v !== undefined && v !== "") as [string, string][],
  )
  return new URLSearchParams(clean).toString()
}

export const reportsApi = {
  dashboard: () => api.get<Dashboard>("/api/reports/dashboard"),
  salesByCustomer: (dateFrom?: string, dateTo?: string) =>
    api.get<ByCustomerRow[]>(`/api/reports/sales-by-customer?${qs({ date_from: dateFrom, date_to: dateTo })}`),
  purchasesBySupplier: (dateFrom?: string, dateTo?: string) =>
    api.get<ByCustomerRow[]>(`/api/reports/purchases-by-supplier?${qs({ date_from: dateFrom, date_to: dateTo })}`),
  stockValuation: () => api.get<StockValuationRow[]>("/api/reports/stock-valuation"),
  aging: (type: "ar" | "ap") => api.get<AgingRow[]>(`/api/reports/aging?invoice_type=${type}`),
  taxSummary: (dateFrom?: string, dateTo?: string) =>
    api.get<TaxSummaryRow[]>(`/api/reports/tax-summary?${qs({ date_from: dateFrom, date_to: dateTo })}`),

  notifications: (unreadOnly = false) =>
    api.get<{ items: Notification[]; total: number }>(
      `/api/core/notifications?limit=20${unreadOnly ? "&unread_only=true" : ""}`,
    ),
  unreadCount: () => api.get<{ count: number }>("/api/core/notifications/unread-count"),
  markRead: (id: string) => api.post<Notification>(`/api/core/notifications/${id}/read`),
}
