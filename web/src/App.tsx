import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { RouterProvider } from "react-router/dom"
import { Toaster } from "sonner"

import { router } from "@/app/router"
import { ThemeProvider } from "@/components/theme-provider"
import { AuthProvider } from "@/lib/auth"

const queryClient = new QueryClient({
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
