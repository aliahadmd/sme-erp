import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, expect, it, vi } from "vitest"

import { PaymentsPage } from "@/features/invoicing/payments-page"
import { setTokenProvider } from "@/lib/api/client"

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ hasPermission: () => true }),
}))

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

it("retries a payment whose committed response was lost without recording another payment", async () => {
  const receipts = new Map<string, { id: string }>()
  const keys: string[] = []
  const bodies: Record<string, unknown>[] = []
  let invoiceBalance = "10.00"
  let loseResponse = true
  setTokenProvider({
    get: () => "accountant-token",
    session: () => 1,
    clear: () => {},
    refresh: async () => false,
  })
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string, options: RequestInit = {}) => {
      const path = new URL(input).pathname
      if (path === "/api/crm/contacts")
        return Response.json({
          items: [
            {
              id: "customer",
              name: "Demo customer",
              currency: "USD",
              is_customer: true,
            },
          ],
          total: 1,
        })
      if (path === "/api/currencies") return Response.json([{ code: "USD" }])
      if (path === "/api/invoicing/statement/customer")
        return Response.json({
          invoices: [
            {
              invoice_id: "invoice",
              invoice_type: "ar",
              currency: "USD",
              balance: invoiceBalance,
              status: invoiceBalance === "0.00" ? "paid" : "posted",
            },
          ],
          currency: "USD",
        })
      if (path === "/api/invoicing/payments" && options.method === "POST") {
        const key = new Headers(options.headers).get("Idempotency-Key")!
        keys.push(key)
        bodies.push(JSON.parse(options.body as string))
        if (!receipts.has(key))
          receipts.set(key, { id: "payment-" + receipts.size })
        if (loseResponse) {
          loseResponse = false
          invoiceBalance = "0.00"
          throw new TypeError("Connection lost after commit")
        }
        return Response.json(receipts.get(key))
      }
      if (path === "/api/invoicing/payments")
        return Response.json({ items: [], total: 0 })
      throw new Error("Unexpected request: " + path)
    })
  )
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  try {
    render(
      <QueryClientProvider client={client}>
        <PaymentsPage />
      </QueryClientProvider>
    )
    await userEvent.click(
      screen.getByRole("button", { name: "Record payment" })
    )
    const dialog = await screen.findByRole("dialog")
    const party = within(dialog).getByText("Party *").parentElement!
    await userEvent.click(within(party).getByRole("combobox"))
    await userEvent.click(
      await screen.findByRole("option", { name: "Demo customer" })
    )
    fireEvent.change(within(dialog).getByLabelText("Amount *"), {
      target: { value: "12.34" },
    })
    const submit = within(dialog).getByRole("button", {
      name: "Record payment",
    })
    await waitFor(() => expect(submit).toBeEnabled())
    await userEvent.click(submit)
    await screen.findByText("Connection lost after commit")
    expect(within(dialog).getByLabelText("Amount *")).toBeDisabled()
    await client.invalidateQueries({ queryKey: ["invoicing", "statement"] })
    await userEvent.click(submit)
    await waitFor(() => expect(keys).toHaveLength(2))
    expect(receipts.size).toBe(1)
    expect(keys[1]).toBe(keys[0])
    expect(bodies[0].allocations).toEqual([
      { invoice_id: "invoice", amount: "10.00" },
    ])
    expect(bodies[1]).toEqual(bodies[0])
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
    )
  } finally {
    client.clear()
  }
})
