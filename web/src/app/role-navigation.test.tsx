import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react"
import { afterAll, afterEach, beforeEach, expect, it, vi } from "vitest"
import { createMemoryRouter } from "react-router"
import { RouterProvider } from "react-router/dom"

import { router as browserRouter } from "@/app/router"
import { api } from "@/lib/api/client"

const session = vi.hoisted(() => ({ permissions: [] as string[] }))
vi.mock("@/components/theme-toggle", () => ({ ThemeToggle: () => null }))
vi.mock("@/lib/auth", () => ({
  useAuth: () => ({
    user: {
      id: "employee-user",
      full_name: "Demo Employee",
      email: "employee@example.com",
    },
    isLoading: false,
    hasPermission: (code: string) => session.permissions.includes(code),
    hasAnyPermission: (codes: string[]) =>
      codes.some((code) => session.permissions.includes(code)),
  }),
}))

let activeRouter: ReturnType<typeof createMemoryRouter>
let client: QueryClient
let fixtures: Record<string, unknown>
const get = vi.spyOn(api, "get")

beforeEach(() => {
  session.permissions = []
  fixtures = {}
  get.mockImplementation(async <T,>(path: string): Promise<T> => {
    const url = new URL(path, "https://erp.example")
    if (url.pathname in fixtures) return fixtures[url.pathname] as T
    const value =
      url.pathname === "/api/hr/employees/me"
        ? { id: "staff", user_id: "employee-user", full_name: "Demo Employee" }
        : url.pathname === "/api/reports/dashboard"
          ? { weeks: [] }
          : /\/(employees|leave-types|leave-requests|taxes|uoms|warehouses|accounts|currencies)$/.test(
                url.pathname
              )
            ? []
            : { items: [], total: 0 }
    return value as T
  })
})
afterEach(() => {
  cleanup()
  activeRouter?.dispose()
  client?.clear()
  get.mockClear()
})
afterAll(() => browserRouter.dispose())

function visit(path: string, permissions: string[]) {
  session.permissions = permissions
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  activeRouter = createMemoryRouter(browserRouter.routes, {
    initialEntries: [path],
  })
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={activeRouter} />
    </QueryClientProvider>
  )
}

it("lands an employee on their leave page without requesting restricted reports", async () => {
  visit("/", ["hr.leave.request"])
  await screen.findByRole("heading", { name: "Leave requests", level: 1 })
  expect(activeRouter.state.location.pathname).toBe("/hr/leave")
  expect(get.mock.calls.some(([path]) => path.startsWith("/api/reports"))).toBe(
    false
  )
  expect(
    within(screen.getByRole("complementary")).queryByRole("link", {
      name: "Dashboard",
    })
  ).toBeNull()
})

it("lands warehouse staff on stock instead of requesting restricted reports", async () => {
  visit("/", ["inventory.stock.read", "inventory.warehouse.read"])
  await screen.findByRole("heading", { name: "Stock on hand", level: 1 })
  expect(get.mock.calls.some(([path]) => path.startsWith("/api/reports"))).toBe(
    false
  )
})

it("blocks a direct product link before mounting its data queries", async () => {
  visit("/products", ["hr.leave.request"])
  await screen.findByRole("heading", { name: /permission for this area/ })
  expect(get.mock.calls.some(([path]) => path.startsWith("/api/catalog"))).toBe(
    false
  )
})

it("opens an accessible settings tab and only queries catalog sections the viewer may read", async () => {
  visit("/settings", ["catalog.tax.read", "core.audit.read"])
  await screen.findByRole("heading", { name: "Catalog settings" })
  await waitFor(() => expect(get).toHaveBeenCalledWith("/api/catalog/taxes"))
  expect(screen.queryByRole("heading", { name: "Units of measure" })).toBeNull()
  expect(screen.queryByRole("button", { name: "Add tax" })).toBeNull()
  const settings = within(screen.getByRole("main")).getByRole("navigation")
  expect(within(settings).queryByRole("link", { name: "Users" })).toBeNull()
  expect(
    get.mock.calls.some(([path]) =>
      /\/api\/(core\/users|catalog\/(uoms|categories))/.test(path)
    )
  ).toBe(false)
})

it.each([
  ["/invoicing", "invoicing.invoice.read", "Customer invoices"],
  ["/accounting", "accounting.entry.read", "Journal entries"],
])("renders an accessible index for %s", async (path, permission, heading) => {
  visit(path, [permission])
  await screen.findByRole("heading", { name: heading, level: 1 })
})

it("keeps the sales Orders tab on the actual orders list", async () => {
  visit("/sales/quotations", ["sales.order.read", "sales.quote.read"])
  fireEvent.click(
    within(screen.getByRole("main")).getByRole("link", { name: "Orders" })
  )
  await waitFor(() =>
    expect(activeRouter.state.location.pathname).toBe("/sales")
  )
  await screen.findByRole("heading", { name: "Sales orders", level: 1 })
})

const product = {
  id: "product",
  sku: "SKU-1",
  name: "Demo product",
  type: "goods",
  status: "active",
  sale_price: "9.00",
  cost_price: "7.00",
  min_stock: "2",
  track_inventory: true,
  sale_tax_id: "sale-tax",
  purchase_tax_id: "purchase-tax",
  uom_id: "unit",
  category_id: "category",
}

it("keeps a product reader's actions and unrequested editor reference data hidden", async () => {
  fixtures["/api/catalog/products"] = { items: [product], total: 1 }
  visit("/products", ["catalog.product.read"])
  await screen.findByText("Demo product")
  for (const name of ["New product", "Edit", "Archive product"]) {
    expect(screen.queryByRole("button", { name })).toBeNull()
  }
  expect(screen.queryByRole("link", { name: "AI drafts" })).toBeNull()
  expect(
    get.mock.calls.some(([path]) =>
      /\/catalog\/(uoms|taxes|categories)/.test(path)
    )
  ).toBe(false)
})

it("preserves assigned product references when an editor cannot read their lookup lists", async () => {
  fixtures["/api/catalog/products"] = { items: [product], total: 1 }
  const patch = vi.spyOn(api, "patch").mockResolvedValue(product)
  try {
    visit("/products", ["catalog.product.read", "catalog.product.update"])
    fireEvent.click(await screen.findByRole("button", { name: "Edit" }))
    fireEvent.click(await screen.findByRole("button", { name: "Save" }))
    await waitFor(() =>
      expect(patch).toHaveBeenCalledWith(
        "/api/catalog/products/product",
        expect.objectContaining({
          sale_tax_id: "sale-tax",
          purchase_tax_id: "purchase-tax",
          uom_id: "unit",
          category_id: "category",
        })
      )
    )
    expect(
      get.mock.calls.some(([path]) =>
        /\/catalog\/(uoms|taxes|categories)/.test(path)
      )
    ).toBe(false)
  } finally {
    patch.mockRestore()
  }
})

it("offers standalone invoices without requesting orders or organization details an accountant cannot read", async () => {
  visit("/invoicing/ar", [
    "invoicing.invoice.read",
    "invoicing.invoice.create",
    "crm.contact.read",
  ])
  fireEvent.click(await screen.findByRole("button", { name: "New invoice" }))
  await screen.findByRole("dialog")
  await waitFor(() =>
    expect(get.mock.calls.some(([path]) => path === "/api/currencies")).toBe(
      true
    )
  )
  expect(screen.queryByText("From order (optional)")).toBeNull()
  expect(
    get.mock.calls.some(([path]) =>
      /\/api\/(sales|purchasing)\/orders|\/api\/org$/.test(path)
    )
  ).toBe(false)
})

it("shows HR approvers their own identity without offering self approval or requesting a missing admin employee", async () => {
  fixtures["/api/hr/employees"] = [
    { id: "staff", user_id: "employee-user", full_name: "Demo Employee" },
  ]
  fixtures["/api/hr/leave-requests"] = [
    {
      id: "leave",
      employee_id: "staff",
      type_id: "annual",
      date_from: "2026-12-01",
      date_to: "2026-12-02",
      days: "2",
      status: "pending",
    },
  ]
  visit("/hr/leave", [
    "hr.employee.read",
    "hr.leave.request",
    "hr.leave.approve",
  ])
  await screen.findByRole("button", { name: "Cancel" })
  expect(screen.queryByRole("button", { name: "Approve" })).toBeNull()
  expect(screen.queryByRole("button", { name: "Reject" })).toBeNull()
  expect(get.mock.calls.some(([path]) => path === "/api/hr/employees/me")).toBe(
    false
  )
})
