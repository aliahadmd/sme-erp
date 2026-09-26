import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { RouterProvider } from "react-router/dom"
import { Toaster, toast } from "sonner"

import { router } from "@/app/router"
import { ThemeProvider } from "@/components/theme-provider"
import { AuthProvider } from "@/lib/auth"
import { ApiError } from "@/lib/api/client"

function onQueryError(error: unknown) {
  if (error instanceof ApiError && error.status === 403) {
    toast.error("Permission denied", {
      description: "Your role doesn't include this action — ask an administrator.",
    })
  }
}

const queryClient = new QueryClient({
  queryCache: new QueryCache({ onError: onQueryError }),
  mutationCache: new MutationCache({ onError: onQueryError }),
  defaultOptions: {
    queries: {
      staleTime: 15_000,
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
})

export function App() {
  return (
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          <RouterProvider router={router} />
        </AuthProvider>
      </QueryClientProvider>
      <Toaster richColors position="top-right" />
    </ThemeProvider>
  )
}

export default App
