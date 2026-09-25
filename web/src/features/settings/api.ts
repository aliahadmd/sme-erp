/** API calls for the settings area (users, roles, permissions, org, audit). */

import { api } from "@/lib/api/client"

export type CurrentUser = {
  id: string
  email: string
  full_name: string
  is_superuser: boolean
  roles: string[]
  permissions: string[]
}

export type UserOut = {
  id: string
  email: string
  full_name: string
  is_active: boolean
  is_superuser: boolean
  default_branch_id: string | null
  last_login_at: string | null
  role_codes: string[]
}

export type Page<T> = {
  items: T[]
  total: number
  limit: number
  offset: number
}

export type RoleOut = {
  id: string
  code: string
  name: string
  description: string | null
  is_system: boolean
  permission_codes: string[]
}

export type PermissionOut = {
  code: string
  module: string
  action: string
  description: string | null
}

export type OrganizationOut = {
  id: string
  name: string
  legal_name: string | null
  tax_id: string | null
  base_currency: string
  email: string | null
  phone: string | null
  address_line1: string | null
  address_line2: string | null
  city: string | null
  country: string | null
}

export type BranchOut = {
  id: string
  code: string
  name: string
  address: string | null
  is_active: boolean
}

export type AuditLogOut = {
  id: string
  user_id: string | null
  action: string
  entity_type: string
  entity_id: string | null
  before: Record<string, unknown> | null
  after: Record<string, unknown> | null
  ip: string | null
  created_at: string
}

export const settingsApi = {
  me: () => api.get<CurrentUser>("/api/auth/me"),

  users: (params: { limit?: number; offset?: number } = {}) =>
    api.get<Page<UserOut>>(`/api/users?${new URLSearchParams(Object.entries(params).map(([k, v]) => [k, String(v)]))}`),
  createUser: (body: {
    email: string
    password: string
    full_name: string
    role_codes: string[]
    is_superuser?: boolean
  }) => api.post<UserOut>("/api/users", body),
  updateUser: (
    id: string,
    body: { full_name?: string; is_active?: boolean; password?: string; role_codes?: string[] },
  ) => api.patch<UserOut>(`/api/users/${id}`, body),

  roles: () => api.get<RoleOut[]>("/api/roles"),
  permissions: () => api.get<PermissionOut[]>("/api/permissions"),
  createRole: (body: {
    code: string
    name: string
    description?: string
    permission_codes: string[]
  }) => api.post<RoleOut>("/api/roles", body),

  organization: () => api.get<OrganizationOut>("/api/org"),
  updateOrganization: (body: Partial<OrganizationOut>) => api.patch<OrganizationOut>("/api/org", body),
  branches: () => api.get<BranchOut[]>("/api/branches"),
  createBranch: (body: { code: string; name: string; address?: string }) =>
    api.post<BranchOut>("/api/branches", body),

  auditLogs: (params: { limit?: number; offset?: number; entity_type?: string; action?: string } = {}) => {
    const clean = Object.fromEntries(
      Object.entries(params).filter(([, v]) => v !== undefined && v !== "") as [string, string][],
    )
    return api.get<Page<AuditLogOut>>(`/api/audit-logs?${new URLSearchParams(clean)}`)
  },
}
