import { useQuery } from "@tanstack/react-query"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, expect, it, vi } from "vitest"

import App from "@/App"
import { api } from "@/lib/api/client"
import { setAccessToken, useAuth } from "@/lib/auth"

vi.mock("@/components/theme-provider", () => ({
  ThemeProvider: ({ children }: { children: React.ReactNode }) => children,
}))
vi.mock("sonner", () => ({ Toaster: () => null, toast: { error: vi.fn() } }))
vi.mock("@/app/router", async () => {
  const { createMemoryRouter } = await import("react-router")
  const { createElement } = await import("react")
  return {
    router: createMemoryRouter([
      { path: "*", element: createElement(SessionProbe) },
    ]),
  }
})

function SessionProbe() {
  const { user, login, logout, isLoading } = useAuth()
  const requests = useQuery({
    queryKey: ["hr", "leave-requests", {}],
    queryFn: () => api.get<string[]>("/api/hr/leave-requests"),
    enabled: !!user,
  })
  if (isLoading) return <p>Restoring session</p>
  return (
    <>
      <p>Current account: {user?.id ?? "signed out"}</p>
      <button onClick={() => void login("admin@example.com", "test-password")}>
        Admin
      </button>
      <button
        onClick={() => void login("employee@example.com", "test-password")}
      >
        Employee
      </button>
      <button onClick={() => void logout()}>Sign out</button>
      {user && requests.data?.map((row) => <p key={row}>{row}</p>)}
    </>
  )
}

beforeEach(() => setAccessToken(null))
afterEach(() => vi.unstubAllGlobals())

it("discards the previous account's cached HR results before another account signs in", async () => {
  let account = ""
  const fetcher = vi.fn(async (url: string, options: RequestInit = {}) => {
    const path = new URL(url).pathname
    if (path === "/api/auth/refresh") return new Response("{}", { status: 401 })
    if (path === "/api/auth/login") {
      account = JSON.parse(options.body as string).email.startsWith("admin")
        ? "admin"
        : "employee"
      return Response.json({ access_token: account + "-token" })
    }
    if (path === "/api/auth/logout") return Response.json({ status: "ok" })
    if (path === "/api/auth/me") {
      return Response.json({
        id: account,
        email: account + "@example.com",
        full_name: account,
        is_superuser: account === "admin",
        roles: [account],
        permissions: ["hr.leave.request"],
      })
    }
    if (path === "/api/hr/leave-requests") {
      return Response.json(
        account === "admin"
          ? ["Other person's leave", "Own leave"]
          : ["Own leave"]
      )
    }
    throw new Error("Unexpected request: " + path)
  })
  vi.stubGlobal("fetch", fetcher)
  render(<App />)
  await screen.findByText("Current account: signed out")
  fireEvent.click(screen.getByRole("button", { name: /^Admin$/ }))
  await screen.findByText("Other person's leave")
  fireEvent.click(screen.getByRole("button", { name: /^Sign out$/ }))
  await screen.findByText("Current account: signed out")
  fireEvent.click(screen.getByRole("button", { name: /^Employee$/ }))
  await screen.findByText("Current account: employee")
  await waitFor(() =>
    expect(screen.queryByText("Other person's leave")).not.toBeInTheDocument()
  )
  await screen.findByText("Own leave")
  expect(
    fetcher.mock.calls.filter(
      ([url]) => new URL(url).pathname === "/api/hr/leave-requests"
    )
  ).toHaveLength(2)
})
