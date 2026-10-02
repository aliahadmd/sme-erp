import { Navigate, NavLink, Outlet } from "react-router"

import { EmployeesPage } from "@/features/hr/hr-page"
import { useAuth } from "@/lib/auth"

const TABS = [
  { to: "/hr", label: "Employees", end: true, permission: "hr.employee.read" },
  { to: "/hr/leave", label: "Leave requests", end: false, permission: "hr.leave.request" },
]

/** /hr index: employee directory for HR staff, own leave for everyone else. */
export function HRIndex() {
  const { hasPermission } = useAuth()
  return hasPermission("hr.employee.read") ? <EmployeesPage /> : <Navigate to="/hr/leave" replace />
}

export function HRLayout() {
  const { hasPermission } = useAuth()
  return (
    <div className="flex flex-col gap-6">
      <nav className="flex gap-1 border-b">
        {TABS.filter((tab) => hasPermission(tab.permission)).map((tab) => (
          <NavLink
            key={tab.to}
            to={tab.to}
            end={tab.end}
            className={({ isActive }) =>
              `-mb-px border-b-2 px-3 py-2 text-sm transition-colors ${
                isActive
                  ? "border-primary font-medium text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              }`
            }
          >
            {tab.label}
          </NavLink>
        ))}
      </nav>
      <Outlet />
    </div>
  )
}
