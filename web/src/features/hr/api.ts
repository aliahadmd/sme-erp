/** HR API — employees, departments, leave. */

import { api } from "@/lib/api/client"

export type Employee = {
  id: string
  number: string
  full_name: string
  work_email: string | null
  department_id: string | null
  position: string | null
  hired_at: string | null
  status: string
}

export type Department = { id: string; name: string }

export type LeaveType = { id: string; name: string; days_per_year: string; accrues: boolean }

export type LeaveRequest = {
  id: string
  employee_id: string
  type_id: string
  date_from: string
  date_to: string
  days: string
  reason: string | null
  status: string
  decided_at: string | null
}

export const hrApi = {
  employees: () => api.get<Employee[]>("/api/hr/employees"),
  /** The signed-in user's own employee record (404 when none is linked). */
  myEmployee: () => api.get<Employee>("/api/hr/employees/me"),
  createEmployee: (body: {
    full_name: string
    work_email?: string
    department_id?: string | null
    position?: string
    hired_at?: string
  }) => api.post<Employee>("/api/hr/employees", body),
  departments: () => api.get<Department[]>("/api/hr/departments"),
  createDepartment: (body: { name: string }) => api.post<Department>("/api/hr/departments", body),
  leaveTypes: () => api.get<LeaveType[]>("/api/hr/leave-types"),
  createLeaveType: (body: { name: string; days_per_year: string }) =>
    api.post<LeaveType>("/api/hr/leave-types", body),
  leaveRequests: (params: { status?: string } = {}) => {
    const clean = Object.fromEntries(
      Object.entries(params).filter(([, v]) => v !== undefined && v !== "") as [string, string][],
    )
    return api.get<LeaveRequest[]>(`/api/hr/leave-requests?${new URLSearchParams(clean)}`)
  },
  createLeaveRequest: (body: {
    employee_id: string
    type_id: string
    date_from: string
    date_to: string
    reason?: string
  }) => api.post<LeaveRequest>("/api/hr/leave-requests", body),
  approveLeave: (id: string) => api.post<LeaveRequest>(`/api/hr/leave-requests/${id}/approve`, undefined),
  rejectLeave: (id: string) => api.post<LeaveRequest>(`/api/hr/leave-requests/${id}/reject`, undefined),
  cancelLeave: (id: string) => api.post<LeaveRequest>(`/api/hr/leave-requests/${id}/cancel`, undefined),
}
