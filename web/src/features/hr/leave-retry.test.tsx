import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, expect, it, vi } from "vitest"

import { LeaveRequestsPage } from "@/features/hr/hr-page"
import { setTokenProvider } from "@/lib/api/client"

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({
    user: { id: "employee-user" },
    hasPermission: (permission: string) => permission === "hr.leave.request",
  }),
}))

afterEach(() => vi.unstubAllGlobals())

it.each(["lost-response", "validation"])(
  "confirms an HR submission after %s without creating another request",
  async (failure) => {
    const receipts = new Map<string, object>()
    const attempts: { key: string; body: string }[] = []
    const employee = {
      id: "employee",
      user_id: "employee-user",
      full_name: "Employee",
      status: "active",
    }
    setTokenProvider({
      get: () => "employee-token",
      session: () => 1,
      clear: () => {},
      refresh: async () => false,
    })
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, options: RequestInit = {}) => {
        const path = new URL(url).pathname
        if (path === "/api/hr/employees/me") return Response.json(employee)
        if (path === "/api/hr/leave-types")
          return Response.json([
            { id: "annual", name: "Annual", days_per_year: "5" },
          ])
        if (path === "/api/hr/leave-requests" && options.method === "POST") {
          const key = new Headers(options.headers).get("Idempotency-Key")!
          attempts.push({ key, body: options.body as string })
          if (failure === "validation" && attempts.length === 1)
            return Response.json(
              {
                error: { code: "validation_error", detail: "No working days" },
              },
              { status: 422 }
            )
          if (!receipts.has(key)) {
            if (receipts.size)
              return Response.json(
                { error: { code: "conflict", detail: "Overlapping request" } },
                { status: 409 }
              )
            receipts.set(key, {
              ...JSON.parse(options.body as string),
              id: "request",
              days: "1",
              status: "pending",
              reason: null,
              decided_at: null,
            })
          }
          if (failure === "lost-response" && attempts.length === 1)
            throw new TypeError("Committed response lost")
          return Response.json(receipts.get(key), { status: 201 })
        }
        if (path === "/api/hr/leave-requests")
          return Response.json([...receipts.values()])
        throw new Error("Unexpected HR request " + path)
      })
    )
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    try {
      const { container } = render(
        <QueryClientProvider client={client}>
          <LeaveRequestsPage />
        </QueryClientProvider>
      )
      await screen.findByRole("option", { name: "Annual" })
      await screen.findByRole("option", { name: "Employee" })
      const type = screen.getAllByRole("combobox")[1]
      fireEvent.change(type, { target: { value: "annual" } })
      const [from, to] =
        container.querySelectorAll<HTMLInputElement>('input[type="date"]')
      for (const input of [from, to])
        fireEvent.change(input, { target: { value: "2031-03-03" } })
      await userEvent.click(
        screen.getByRole("button", { name: "Submit" })
      )
      if (failure === "lost-response") {
        const retry = await screen.findByRole("button", {
          name: "Retry submitted request",
        })
        expect(from).toBeDisabled()
        expect(to).toBeDisabled()
        expect(type).toBeDisabled()
        expect(screen.getByRole("status")).toHaveTextContent("unconfirmed")
        await userEvent.click(retry)
      } else {
        await screen.findByRole("alert")
        expect(from).toBeEnabled()
        expect(type).toBeEnabled()
        for (const input of [from, to])
          fireEvent.change(input, { target: { value: "2031-03-04" } })
        await userEvent.click(
          screen.getByRole("button", { name: "Submit" })
        )
      }
      await screen.findByText("pending", { exact: true })
      await waitFor(() => expect(attempts).toHaveLength(2))
      expect(receipts.size).toBe(1)
      if (failure === "lost-response") expect(attempts[1]).toEqual(attempts[0])
      else {
        expect(attempts[1].key).not.toBe(attempts[0].key)
        expect(attempts[1].body).toContain("2031-03-04")
      }
      expect(type).toBeEnabled()
    } finally {
      client.clear()
    }
  }
)
