import { Navigate, Outlet, useLocation } from "react-router"

import { AppShell } from "@/app/shell"
import { useAuth } from "@/lib/auth"

export function RequireAuth() {
  const { user, isLoading } = useAuth()
  const location = useLocation()

  if (isLoading) {
    return (
      <div className="flex min-h-svh items-center justify-center text-sm text-muted-foreground">
        Loading…
      </div>
    )
  }
  if (!user) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />
  }
  return (
    <AppShell>
      <Outlet />
    </AppShell>
  )
}
