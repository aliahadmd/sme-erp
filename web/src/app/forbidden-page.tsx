import { Link } from "react-router"

import { Button } from "@/components/ui/button"

export function ForbiddenPage() {
  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center gap-3 rounded-lg border border-dashed p-8 text-center">
      <div className="text-4xl font-semibold text-destructive">403</div>
      <h1 className="text-xl font-semibold">You don&apos;t have permission for this area</h1>
      <p className="max-w-md text-sm text-muted-foreground">
        Your role doesn&apos;t include the permission this page requires. Ask an
        administrator to grant it, or head back to the dashboard.
      </p>
      <Link to="/">
        <Button variant="outline">Back to dashboard</Button>
      </Link>
    </div>
  )
}
