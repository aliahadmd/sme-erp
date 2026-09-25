/** CRM contacts API. */

import { api } from "@/lib/api/client"
import type { Page } from "@/features/settings/api"

export type Contact = {
  id: string
  code: string
  kind: string
  name: string
  legal_name: string | null
  tax_id: string | null
  is_customer: boolean
  is_supplier: boolean
  emails: { label?: string; value?: string }[]
  phones: { label?: string; value?: string }[]
  addresses: Record<string, string>[]
  currency: string
  payment_terms_days: number
  credit_limit: string
  tags: string[]
  notes: string | null
  status: string
}

export type ContactInput = {
  name: string
  kind?: string
  legal_name?: string | null
  tax_id?: string | null
  is_customer: boolean
  is_supplier: boolean
  emails?: { label?: string; value?: string }[]
  phones?: { label?: string; value?: string }[]
  addresses?: Record<string, string>[]
  currency?: string
  payment_terms_days?: number
  credit_limit?: string
  tags?: string[]
  notes?: string | null
}

export const contactsApi = {
  list: (params: { type?: string; q?: string; tag?: string; limit?: number; offset?: number } = {}) => {
    const clean = Object.fromEntries(
      Object.entries(params).filter(([, v]) => v !== undefined && v !== "") as [string, string][],
    )
    return api.get<Page<Contact>>(`/api/crm/contacts?${new URLSearchParams(clean)}`)
  },
  get: (id: string) => api.get<Contact>(`/api/crm/contacts/${id}`),
  create: (body: ContactInput) => api.post<Contact>("/api/crm/contacts", body),
  update: (id: string, body: Partial<ContactInput>) => api.patch<Contact>(`/api/crm/contacts/${id}`, body),
  archive: (id: string, archived: boolean) =>
    api.post<Contact>(`/api/crm/contacts/${id}/archive`, { archived }),
}
