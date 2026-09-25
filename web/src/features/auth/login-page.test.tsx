import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router"
import { describe, expect, it, vi } from "vitest"

import { LoginPage } from "./login-page"
import { AuthProvider } from "@/lib/auth"

function renderLogin() {
  return render(
    <MemoryRouter initialEntries={["/login"]}>
      <AuthProvider>
        <LoginPage />
      </AuthProvider>
    </MemoryRouter>,
  )
}

describe("LoginPage", () => {
  it("renders the form", () => {
    renderLogin()
    expect(screen.getByLabelText("Email")).toBeInTheDocument()
    expect(screen.getByLabelText("Password")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /sign in/i })).toBeInTheDocument()
  })

  it("shows validation errors for empty submit", async () => {
    const user = userEvent.setup()
    renderLogin()
    await user.click(screen.getByRole("button", { name: /sign in/i }))
    expect(await screen.findByText("Email is required")).toBeInTheDocument()
    expect(screen.getByText("Password is required")).toBeInTheDocument()
  })

  it("shows a friendly error when the auth API is unavailable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.reject(new TypeError("Failed to fetch"))),
    )
    const user = userEvent.setup()
    renderLogin()
    await user.type(screen.getByLabelText("Email"), "admin@example.com")
    await user.type(screen.getByLabelText("Password"), "secret")
    await user.click(screen.getByRole("button", { name: /sign in/i }))
    expect(await screen.findByText(/could not reach the api/i)).toBeInTheDocument()
    vi.unstubAllGlobals()
  })
})
