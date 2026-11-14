import { RouterProvider } from "react-router/dom"
import { Toaster } from "sonner"

import { router } from "@/app/router"
import { SessionQueries } from "@/app/session-queries"
import { ThemeProvider } from "@/components/theme-provider"
import { AuthProvider } from "@/lib/auth"

export function App() {
  return (
    <ThemeProvider>
      <AuthProvider>
        <SessionQueries>
          <RouterProvider router={router} />
        </SessionQueries>
      </AuthProvider>
      <Toaster richColors position="top-right" />
    </ThemeProvider>
  )
}

export default App
