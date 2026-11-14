import {
  MutationCache,
  QueryCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query"
import { useEffect, useState, type ReactNode } from "react"
import { toast } from "sonner"

import { ApiError } from "@/lib/api/client"
import { useAuth } from "@/lib/auth"

function onQueryError(error: unknown) {
  if (error instanceof ApiError && error.status === 403) {
    toast.error("Permission denied", {
      description:
        "Your role doesn't include this action — ask an administrator.",
    })
  }
}

function QuerySession({ children }: { children: ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        queryCache: new QueryCache({ onError: onQueryError }),
        mutationCache: new MutationCache({ onError: onQueryError }),
        defaultOptions: {
          queries: { staleTime: 15_000, retry: 1, refetchOnWindowFocus: false },
        },
      })
  )
  useEffect(() => () => client.clear(), [client])
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

/** Recreate the cache and page state before rendering another account. */
export function SessionQueries({ children }: { children: ReactNode }) {
  const { user } = useAuth()
  return <QuerySession key={user?.id ?? "signed-out"}>{children}</QuerySession>
}
