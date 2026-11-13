import type { Statement } from "@/features/invoicing/api"
import { moneyToCents } from "@/lib/money"

/** Only normal invoices on this payment's side and in its currency can settle. */
export function openInvoicesFor(
  statement: Statement | undefined,
  direction: "in" | "out",
  currency?: string
) {
  const side = direction === "in" ? "ar" : "ap"
  return (statement?.invoices ?? []).filter(
    (line) =>
      line.invoice_type === side &&
      moneyToCents(line.balance) > 0n &&
      (!currency || !line.currency || line.currency === currency)
  )
}
