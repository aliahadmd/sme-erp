import { useNavigate, useRouteError, isRouteErrorResponse } from "react-router"

import { Button } from "@/components/ui/button"

export function ErrorPage() {
  const error = useRouteError()
  const navigate = useNavigate()
  const message = isRouteErrorResponse(error)
    ? `${error.status} ${error.statusText}`
    : "Something went wrong while rendering this page."

  return (
    <div className="flex min-h-svh flex-col items-center justify-center gap-3 p-8 text-center">
      <h1 className="text-2xl font-semibold tracking-tight">Oops</h1>
      <p className="max-w-md text-sm text-muted-foreground">{message}</p>
      <div className="flex gap-2">
        <Button variant="outline" onClick={() => navigate(-1)}>
          Go back
        </Button>
        <Button onClick={() => navigate("/")}>Go to dashboard</Button>
      </div>
    </div>
  )
}
