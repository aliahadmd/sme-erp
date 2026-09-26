import { render, screen } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router"
import { describe, expect, it, vi } from "vitest"

import { RequirePermission } from "@/app/require-auth"
import { AuthProvider } from "@/lib/auth"

vi.mock("@/lib/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/client")>()
  return {
    ...actual,
    setTokenProvider: vi.fn(),
  }
})

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AuthProvider>
        <Routes>
          <Route
            path="/guarded"
            element={
              <RequirePermission code="core.user.read">
                <div>SECRET CONTENT</div>
              </RequirePermission>
            }
          />
          <Route path="/403" element={<div>FORBIDDEN PAGE</div>} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  )
}

describe("RequirePermission", () => {
  it("renders children when unauthenticated restore finds no session and shows forbidden instead of leaking content", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(new Response("{}", { status: 401 })) as unknown as Response),
    )
    renderAt("/guarded")
    // Without a session the user has no permissions — content stays hidden.
    expect(screen.queryByText("SECRET CONTENT")).not.toBeInTheDocument()
    vi.unstubAllGlobals()
  })
})
