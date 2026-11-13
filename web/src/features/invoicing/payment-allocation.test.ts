import { expect, it } from "vitest"

import type { Statement } from "@/features/invoicing/api"
import { openInvoicesFor } from "@/features/invoicing/payment-allocation"
import { allocateFifo } from "@/lib/money"

it("keeps FIFO allocations on the payment's side and currency", () => {
  const lines = [
    {
      invoice_id: "old-eur",
      invoice_type: "ar",
      currency: "EUR",
      balance: "90.00",
    },
    {
      invoice_id: "bill-usd",
      invoice_type: "ap",
      currency: "USD",
      balance: "80.00",
    },
    {
      invoice_id: "credit-usd",
      invoice_type: "ar_credit",
      currency: "USD",
      balance: "-5.00",
    },
    {
      invoice_id: "first-usd",
      invoice_type: "ar",
      currency: "USD",
      balance: "0.10",
    },
    {
      invoice_id: "second-usd",
      invoice_type: "ar",
      currency: "USD",
      balance: "0.30",
    },
  ] as Statement["invoices"]
  const statement = { invoices: lines } as Statement
  const allocations = allocateFifo(
    "0.30",
    openInvoicesFor(statement, "in", "USD"),
    (l) => l.balance
  )
  expect(
    allocations.map((line) => [line.item.invoice_id, line.amount])
  ).toEqual([
    ["first-usd", "0.10"],
    ["second-usd", "0.20"],
  ])
  expect(
    openInvoicesFor(statement, "out", "USD").map((line) => line.invoice_id)
  ).toEqual(["bill-usd"])
  expect(openInvoicesFor(statement, "in", "JPY")).toEqual([])
})
