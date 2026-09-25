/** Accounting API. */

import { api } from "@/lib/api/client"
import type { Page } from "@/features/settings/api"

export type Account = {
  id: string
  code: string
  name: string
  type: string
  is_system: boolean
  is_active: boolean
}

export type JournalEntry = {
  id: string
  number: string
  entry_date: string
  memo: string | null
  source_type: string
  lines: {
    id: string
    account_code: string | null
    account_name: string | null
    debit: string
    credit: string
  }[]
}

export type TrialBalanceRow = {
  account_id: string
  code: string
  name: string
  type: string
  total_debit: string
  total_credit: string
  balance: string
}

export const accountingApi = {
  accounts: () => api.get<Account[]>("/api/accounting/accounts"),
  createAccount: (body: { code: string; name: string; type: string }) =>
    api.post<Account>("/api/accounting/accounts", body),
  journalEntries: (params: { limit?: number } = {}) => {
    const clean = new URLSearchParams()
    if (params.limit) clean.set("limit", String(params.limit))
    return api.get<Page<JournalEntry>>(`/api/accounting/journal-entries?${clean}`)
  },
  createEntry: (body: {
    memo: string
    lines: { account_id: string; debit: string; credit: string }[]
  }) => api.post<JournalEntry>("/api/accounting/journal-entries", body),
  trialBalance: () => api.get<TrialBalanceRow[]>("/api/accounting/trial-balance"),
}
