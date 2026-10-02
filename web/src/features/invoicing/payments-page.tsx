import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { toast } from "sonner"

import { invoicingApi, type Payment, type Statement } from "@/features/invoicing/api"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { ApiError } from "@/lib/api/client"
import { allocateFifo, formatMoney, moneyToCents, sumMoney } from "@/lib/money"

function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : "Something went wrong"
}

type StatementLine = Statement["invoices"][number]

/** Open invoices of the side a payment settles (AR for money in, AP for out). */
function openInvoicesFor(statement: Statement | undefined, direction: "in" | "out") {
  const side = direction === "in" ? "ar" : "ap"
  return (statement?.invoices ?? []).filter(
    (line) => line.invoice_type === side && moneyToCents(line.balance) > 0n,
  )
}

/** Posted credit notes with a refundable balance (statement shows them negative). */
function refundableCredits(statement: Statement | undefined) {
  return (statement?.invoices ?? []).filter(
    (line) => line.invoice_type.endsWith("_credit") && moneyToCents(line.balance) < 0n,
  )
}

export function PaymentsPage() {
  const queryClient = useQueryClient()
  const [recordOpen, setRecordOpen] = useState(false)
  const [allocating, setAllocating] = useState<Payment | null>(null)

  const { data, isLoading, refetch } = useQuery({
    queryKey: ["invoicing", "payments", {}],
    queryFn: () => invoicingApi.payments({ limit: 50 }),
  })
  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["invoicing"] })
    void refetch()
  }

  const voidPay = useMutation({
    mutationFn: (id: string) => invoicingApi.voidPayment(id),
    onSuccess: () => {
      toast.success("Payment voided — balances restored, reversal entry posted")
      invalidate()
    },
    onError: (err) => toast.error(errorMessage(err)),
  })

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Payments</h1>
          <p className="text-sm text-muted-foreground">
            Money in (customer) and out (supplier), allocated to invoices — or refunds of credit
            notes
          </p>
        </div>
        <Button onClick={() => setRecordOpen(true)}>Record payment</Button>
      </div>

      <div className="rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Number</TableHead>
              <TableHead>Direction</TableHead>
              <TableHead>Party</TableHead>
              <TableHead>Date</TableHead>
              <TableHead>Method</TableHead>
              <TableHead className="text-right">Amount</TableHead>
              <TableHead className="text-right">Unallocated</TableHead>
              <TableHead>Status</TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {isLoading && (
              <TableRow>
                <TableCell colSpan={9} className="text-center text-muted-foreground">
                  Loading…
                </TableCell>
              </TableRow>
            )}
            {!isLoading && data?.items.length === 0 && (
              <TableRow>
                <TableCell colSpan={9} className="text-center text-muted-foreground">
                  No payments recorded
                </TableCell>
              </TableRow>
            )}
            {data?.items.map((payment) => (
              <TableRow key={payment.id}>
                <TableCell className="font-mono text-xs">
                  {payment.number}
                  {payment.credit_note_id && (
                    <Badge variant="secondary" className="ml-1.5">
                      refund
                    </Badge>
                  )}
                </TableCell>
                <TableCell>
                  <Badge variant={payment.direction === "in" ? "default" : "secondary"}>
                    {payment.direction}
                  </Badge>
                </TableCell>
                <TableCell>{payment.party_name}</TableCell>
                <TableCell className="text-sm">{payment.payment_date}</TableCell>
                <TableCell className="text-sm">{payment.method}</TableCell>
                <TableCell className="text-right font-mono text-sm">
                  {formatMoney(payment.amount)} {payment.currency}
                </TableCell>
                <TableCell className="text-right font-mono text-sm">
                  {moneyToCents(payment.unallocated) > 0n ? formatMoney(payment.unallocated) : "—"}
                </TableCell>
                <TableCell>
                  <Badge variant={payment.status === "void" ? "destructive" : "outline"}>
                    {payment.status}
                  </Badge>
                </TableCell>
                <TableCell>
                  <div className="flex flex-wrap justify-end gap-1">
                    {payment.status === "recorded" && moneyToCents(payment.unallocated) > 0n && (
                      <Button size="sm" variant="outline" onClick={() => setAllocating(payment)}>
                        Allocate
                      </Button>
                    )}
                    {payment.status === "recorded" && (
                      <Button size="sm" variant="ghost" onClick={() => voidPay.mutate(payment.id)}>
                        Void
                      </Button>
                    )}
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      <RecordPaymentDialog open={recordOpen} onOpenChange={setRecordOpen} onDone={invalidate} />
      <AllocateDialog payment={allocating} onClose={() => setAllocating(null)} onDone={invalidate} />
    </div>
  )
}

function AllocationPreview({
  lines,
  amount,
}: {
  lines: { item: StatementLine; amount: string }[]
  amount: string
}) {
  return (
    <div className="rounded-md border p-3">
      <div className="mb-2 flex items-center justify-between text-sm">
        <span className="font-medium">Open invoices (FIFO allocation)</span>
        <span className="text-muted-foreground">
          {sumMoney(lines.map((l) => l.amount))} / {formatMoney(amount || "0")}
        </span>
      </div>
      {lines.length === 0 && (
        <p className="text-sm text-muted-foreground">
          Nothing to allocate — the amount stays on account
        </p>
      )}
      <div className="flex flex-col gap-1">
        {lines.map(({ item, amount: take }) => (
          <div key={item.invoice_id} className="flex justify-between text-sm">
            <span className="font-mono text-xs">
              {item.number} (open {formatMoney(item.balance)})
            </span>
            <span className="font-mono">{take}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

function RecordPaymentDialog({
  open,
  onOpenChange,
  onDone,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onDone: () => void
}) {
  const [mode, setMode] = useState<"payment" | "refund">("payment")
  const [direction, setDirection] = useState<"in" | "out">("in")
  const [partyId, setPartyId] = useState("")
  const [creditNoteId, setCreditNoteId] = useState("")
  const [amount, setAmount] = useState("")
  const [method, setMethod] = useState("bank")
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  const contactsQuery = useQuery({
    queryKey: ["invoicing", "payment-contacts", open],
    queryFn: () => invoicingApi.contacts(),
    enabled: open,
  })
  const contacts = (contactsQuery.data?.items ?? []).filter((c) =>
    mode === "refund" ? true : direction === "in" ? c.is_customer : c.is_supplier,
  )

  const statementQuery = useQuery({
    queryKey: ["invoicing", "statement", partyId, open],
    queryFn: () => invoicingApi.statement(partyId),
    enabled: open && !!partyId,
  })
  const statement = statementQuery.data
  const credits = refundableCredits(statement)
  const creditNote = credits.find((c) => c.invoice_id === creditNoteId)
  const allocations =
    mode === "payment"
      ? allocateFifo(amount || "0", openInvoicesFor(statement, direction), (l) => l.balance)
      : []

  const reset = () => {
    setPartyId("")
    setCreditNoteId("")
    setAmount("")
  }

  const save = async () => {
    setSaving(true)
    setError(null)
    try {
      if (mode === "refund") {
        if (!creditNote) throw new Error("Select a credit note")
        await invoicingApi.recordPayment({
          // AR credit note → we pay the customer; AP credit note → supplier pays us.
          direction: creditNote.invoice_type === "ar_credit" ? "out" : "in",
          party_id: partyId,
          amount,
          method,
          credit_note_id: creditNote.invoice_id,
          allocations: [],
        })
        toast.success("Refund recorded")
      } else {
        await invoicingApi.recordPayment({
          direction,
          party_id: partyId,
          amount,
          method,
          allocations: allocations.map((a) => ({ invoice_id: a.item.invoice_id, amount: a.amount })),
        })
        toast.success("Payment recorded")
      }
      onDone()
      onOpenChange(false)
      reset()
    } catch (err) {
      setError(err instanceof Error && !(err instanceof ApiError) ? err.message : errorMessage(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{mode === "refund" ? "Refund a credit note" : "Record payment"}</DialogTitle>
        </DialogHeader>
        <div className="flex flex-col gap-4">
          <div className="grid grid-cols-2 gap-4">
            <div className="flex flex-col gap-2">
              <Label>Type</Label>
              <Select
                value={mode === "refund" ? "refund" : direction}
                onValueChange={(v) => {
                  if (v === "refund") {
                    setMode("refund")
                  } else {
                    setMode("payment")
                    setDirection((v as "in" | "out") ?? "in")
                  }
                  reset()
                }}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="in">Money in (customer)</SelectItem>
                  <SelectItem value="out">Money out (supplier)</SelectItem>
                  <SelectItem value="refund">Refund of a credit note</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="flex flex-col gap-2">
              <Label>Method</Label>
              <Select value={method} onValueChange={(v) => setMethod(v ?? "bank")}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="cash">Cash</SelectItem>
                  <SelectItem value="bank">Bank</SelectItem>
                  <SelectItem value="card">Card</SelectItem>
                  <SelectItem value="transfer">Transfer</SelectItem>
                  <SelectItem value="other">Other</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div className="flex flex-col gap-2">
              <Label>Party *</Label>
              <Select
                value={partyId}
                onValueChange={(v) => {
                  setPartyId(v ?? "")
                  setCreditNoteId("")
                }}
              >
                <SelectTrigger>
                  <SelectValue placeholder="Select…" />
                </SelectTrigger>
                <SelectContent>
                  {contacts.map((c) => (
                    <SelectItem key={c.id} value={c.id}>
                      {c.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor="pay-amount">Amount *</Label>
              <Input
                id="pay-amount"
                type="number"
                step="0.01"
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
              />
            </div>
          </div>

          {mode === "refund" && partyId && (
            <div className="flex flex-col gap-2">
              <Label>Credit note *</Label>
              <Select
                value={creditNoteId}
                onValueChange={(v) => {
                  const id = v ?? ""
                  setCreditNoteId(id)
                  const picked = credits.find((c) => c.invoice_id === id)
                  if (picked) setAmount(formatMoney(picked.balance).replace("-", ""))
                }}
              >
                <SelectTrigger>
                  <SelectValue placeholder={credits.length ? "Select…" : "No refundable credit"} />
                </SelectTrigger>
                <SelectContent>
                  {credits.map((c) => (
                    <SelectItem key={c.invoice_id} value={c.invoice_id}>
                      {c.number} — refundable {formatMoney(c.balance).replace("-", "")}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}

          {mode === "payment" && partyId && <AllocationPreview lines={allocations} amount={amount} />}
          {error && <p className="text-xs text-destructive">{error}</p>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            disabled={!partyId || !amount || saving || (mode === "refund" && !creditNoteId)}
            onClick={() => void save()}
          >
            {saving ? "Saving…" : mode === "refund" ? "Record refund" : "Record payment"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function AllocateDialog({
  payment,
  onClose,
  onDone,
}: {
  payment: Payment | null
  onClose: () => void
  onDone: () => void
}) {
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const statementQuery = useQuery({
    queryKey: ["invoicing", "statement", payment?.party_id, "allocate"],
    queryFn: () => invoicingApi.statement(payment?.party_id ?? ""),
    enabled: !!payment,
  })
  const direction = (payment?.direction ?? "in") as "in" | "out"
  const lines = payment
    ? allocateFifo(
        payment.unallocated,
        openInvoicesFor(statementQuery.data, direction),
        (line) => line.balance,
      )
    : []

  const save = async () => {
    if (!payment) return
    setSaving(true)
    setError(null)
    try {
      await invoicingApi.allocatePayment(
        payment.id,
        lines.map((l) => ({ invoice_id: l.item.invoice_id, amount: l.amount })),
      )
      toast.success("Payment allocated")
      onDone()
      onClose()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={!!payment} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Allocate {payment?.number}</DialogTitle>
        </DialogHeader>
        <AllocationPreview lines={lines} amount={payment?.unallocated ?? "0"} />
        {error && <p className="text-xs text-destructive">{error}</p>}
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button disabled={!lines.length || saving} onClick={() => void save()}>
            {saving ? "Allocating…" : "Allocate"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
