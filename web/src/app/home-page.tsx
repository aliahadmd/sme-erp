import { Navigate } from "react-router"

import { ForbiddenPage } from "@/app/forbidden-page"
import { NAV_ITEMS } from "@/app/nav-items"
import { DashboardPage } from "@/features/dashboard/dashboard-page"
import { useAuth } from "@/lib/auth"

export function HomePage() {
  const { hasPermission, hasAnyPermission } = useAuth()
  if (hasPermission("reports.view")) return <DashboardPage />
  const candidates = ["/hr", "/inventory", ...NAV_ITEMS.map((item) => item.to)]
  const destination = candidates.find((path) => {
    const item = NAV_ITEMS.find((item) => item.to === path)
    return (
      path !== "/" &&
      item?.anyPermission &&
      hasAnyPermission(item.anyPermission)
    )
  })
  return destination ? <Navigate to={destination} replace /> : <ForbiddenPage />
}
