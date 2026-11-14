import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, expect, it, vi } from "vitest"

import { JournalPage } from "@/features/accounting/journal-and-trial-page"
import { AdjustmentsPage } from "@/features/inventory/inventory-pages"
import { setTokenProvider } from "@/lib/api/client"

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ hasPermission: () => true }),
}))

afterEach(() => vi.unstubAllGlobals())

function mount(page: React.ReactNode) {
  setTokenProvider({
    get: () => "accountant-token",
    session: () => 1,
    clear: () => {},
    refresh: async () => false,
  })
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(<QueryClientProvider client={client}>{page}</QueryClientProvider>)
  return client
}

async function select(dialog: HTMLElement, label: string, option: string) {
  const control = within(dialog).getByText(label).parentElement!
  await userEvent.click(within(control).getByRole("combobox"))
  await userEvent.click(await screen.findByRole("option", { name: option }))
}

it("retries a committed manual journal entry without posting another entry", async () => {
  const entries = new Map<string, object>()
  const attempts: { key: string; body: string }[] = []
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, options: RequestInit = {}) => {
      const path = new URL(url).pathname
      if (path === "/api/accounting/accounts")
        return Response.json([
          { id: "cash", code: "1000", name: "Cash" },
          { id: "capital", code: "3000", name: "Capital" },
        ])
      if (
        path === "/api/accounting/journal-entries" &&
        options.method === "POST"
      ) {
        const key = new Headers(options.headers).get("Idempotency-Key")!
        attempts.push({ key, body: options.body as string })
        if (!entries.has(key)) entries.set(key, { id: "entry-" + entries.size })
        if (attempts.length === 1)
          throw new TypeError("Committed response lost")
        return Response.json(entries.get(key))
      }
      if (path === "/api/accounting/journal-entries")
        return Response.json({ items: [], total: 0 })
      throw new Error("Unexpected request " + path)
    })
  )
  const client = mount(<JournalPage />)
  try {
    await userEvent.click(
      screen.getByRole("button", { name: "New manual entry" })
    )
    const dialog = await screen.findByRole("dialog")
    fireEvent.change(within(dialog).getByLabelText("Memo"), {
      target: { value: "Opening capital" },
    })
    await select(dialog, "Debit account", "1000 · Cash")
    await select(dialog, "Credit account", "3000 · Capital")
    const amount = within(dialog).getByLabelText("Amount")
    fireEvent.change(amount, { target: { value: "12.34" } })
    const submit = within(dialog).getByRole("button", { name: "Post entry" })
    await userEvent.click(submit)
    await within(dialog).findByText("Something went wrong")
    expect(amount).toBeDisabled()
    expect(
      within(dialog).getByRole("button", { name: "Retry submitted entry" })
    ).toBeEnabled()
    await userEvent.click(submit)
    await waitFor(() => expect(attempts).toHaveLength(2))
    expect(entries.size).toBe(1)
    expect(attempts[1]).toEqual(attempts[0])
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
    )
  } finally {
    client.clear()
  }
})

it.each(["create", "post", "post-rejected"])(
  "resumes an adjustment after a %s response failure without creating a second draft or stock movement",
  async (failure) => {
    const drafts = new Map<string, { id: string }>()
    const postings = new Map<string, { id: string }>()
    const creates: { key: string; body: string }[] = []
    const posts: { key: string; path: string }[] = []
    let lost = false
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, options: RequestInit = {}) => {
        const path = new URL(url).pathname
        if (path === "/api/catalog/products")
          return Response.json({
            items: [{ id: "product", name: "Cable", sku: "CAB" }],
            total: 1,
          })
        if (
          path === "/api/inventory/adjustments" &&
          options.method === "POST"
        ) {
          const key = new Headers(options.headers).get("Idempotency-Key")!
          creates.push({ key, body: options.body as string })
          if (!drafts.has(key))
            drafts.set(key, { id: "adjustment-" + drafts.size })
          if (failure === "create" && !lost) {
            lost = true
            throw new TypeError("Created response lost")
          }
          return Response.json(drafts.get(key))
        }
        if (/^\/api\/inventory\/adjustments\/[^/]+\/post$/.test(path)) {
          const key = new Headers(options.headers).get("Idempotency-Key")!
          posts.push({ key, path })
          if (failure === "post-rejected" && !lost) {
            lost = true
            return Response.json(
              {
                error: {
                  code: "insufficient_stock",
                  detail: "Stock changed; replenish before posting",
                },
              },
              { status: 409 }
            )
          }
          if (!postings.has(key)) postings.set(key, { id: path.split("/")[4] })
          if (failure === "post" && !lost) {
            lost = true
            throw new TypeError("Posted response lost")
          }
          return Response.json(postings.get(key))
        }
        if (path === "/api/inventory/adjustments")
          return Response.json({ items: [], total: 0 })
        throw new Error("Unexpected request " + path)
      })
    )
    const client = mount(<AdjustmentsPage />)
    try {
      await userEvent.click(
        screen.getByRole("button", { name: "New adjustment" })
      )
      let dialog = await screen.findByRole("dialog")
      await select(dialog, "Product", "Cable (CAB)")
      fireEvent.change(
        within(dialog).getByLabelText("Quantity (negative for shrinkage)"),
        { target: { value: "-1.0000" } }
      )
      await userEvent.click(
        within(dialog).getByRole("button", { name: "Create & post" })
      )
      await within(dialog).findByText(
        failure === "post-rejected"
          ? "Stock changed; replenish before posting"
          : "Something went wrong"
      )
      await userEvent.click(
        within(dialog).getByRole("button", { name: "Cancel" })
      )
      await waitFor(() =>
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
      )
      await userEvent.click(
        screen.getByRole("button", { name: "New adjustment" })
      )
      dialog = await screen.findByRole("dialog")
      expect(
        within(dialog).getByLabelText("Quantity (negative for shrinkage)")
      ).toBeDisabled()
      expect(
        within(dialog).getByLabelText("Quantity (negative for shrinkage)")
      ).toHaveValue(-1)
      await userEvent.click(
        within(dialog).getByRole("button", {
          name: /Create & post|Retry submitted adjustment/,
        })
      )
      await waitFor(() =>
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
      )
      expect(drafts.size).toBe(1)
      expect(postings.size).toBe(1)
      if (failure === "create") expect(creates[1]).toEqual(creates[0])
      else expect(creates).toHaveLength(1)
      if (failure === "post") expect(posts[1]).toEqual(posts[0])
      expect(new Set(posts.map((p) => p.path)).size).toBe(1)
    } finally {
      client.clear()
    }
  }
)
