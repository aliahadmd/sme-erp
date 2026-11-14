import type { ReactNode } from "react"
import { Navigate, NavLink, Outlet, useLocation } from "react-router"

import { ForbiddenPage } from "@/app/forbidden-page"
import type { SectionTab } from "@/app/section-tabs"
import { useAuth } from "@/lib/auth"

function useVisibleTabs(tabs: SectionTab[]) {
  const { hasAnyPermission } = useAuth()
  return tabs.filter((tab) => hasAnyPermission([tab.permission].flat()))
}

export function SectionLayout({ tabs }: { tabs: SectionTab[] }) {
  const visible = useVisibleTabs(tabs)
  return (
    <div className="flex flex-col gap-6">
      <nav className="flex gap-1 border-b">
        {visible.map((tab) => (
          <NavLink
            key={tab.to}
            to={tab.to}
            end={tab.end}
            className={({ isActive }) =>
              `-mb-px border-b-2 px-3 py-2 text-sm transition-colors ${isActive ? "border-primary font-medium text-foreground" : "border-transparent text-muted-foreground hover:text-foreground"}`
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

/** Render the section's index content or open its first permitted tab. */
export function SectionIndex({
  tabs,
  children,
}: {
  tabs: SectionTab[]
  children?: ReactNode
}) {
  const visible = useVisibleTabs(tabs)
  const { pathname } = useLocation()
  if (children && visible.some((tab) => tab.to === pathname))
    return <>{children}</>
  const destination = visible.find((tab) => tab.to !== pathname)
  return destination ? (
    <Navigate to={destination.to} replace />
  ) : (
    <ForbiddenPage />
  )
}
