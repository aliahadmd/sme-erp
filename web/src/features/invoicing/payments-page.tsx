import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { toast } from "sonner"

import { invoicingApi, type Statement } from "@/features/invoicing/api"
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

function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : "Something went wrong"
}

export function PaymentsPage() {
  const queryClient = useQueryClient()
  const [recordOpen, setRecordOpen] = useState(false)

  const { data, isLoading, refetch } = useQuery({
    queryKey: ["invoicing", "payments", {}],
    queryFn: () => invoicingApi.payments({ limit: 50 }),
  })
  const invalidate = () => void queryClient.invalidateQueries({ queryKey: ["invoicing"] })

  const voidPay = useMutation({
    mutationFn: (id: string) => invoicingApi.voidPayment(id),
    onSuccess: () => {
      toast.success("Payment voided — invoice balances restored")
      invalidate()
      void refetch()
    },
    onError: (err) => toast.error(errorMessage(err)),
  })

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Payments</h1>
          <p className="text-sm text-muted-foreground">
            Money in (customer) and out (supplier), allocated to invoices
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
              <TableHead>Status</TableHead>
              <TableHead className="w-24" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {isLoading && (
              <TableRow>
                <TableCell colSpan={8} className="text-center text-muted-foreground">
                  Loading…
                </TableCell>
              </TableRow>
            )}
            {!isLoading && data?.items.length === 0 && (
              <TableRow>
                <TableCell colSpan={8} className="text-center text-muted-foreground">
                  No payments recorded
                </TableCell>
              </TableRow>
            )}
            {data?.items.map((payment) => (
              <TableRow key={payment.id}>
                <TableCell className="font-mono text-xs">{payment.number}</TableCell>
                <TableCell>
                  <Badge variant={payment.direction === "in" ? "default" : "secondary"}>
                    {payment.direction === "in" ? "in" : "out"}
                  </Badge>
                </TableCell>
                <TableCell>{payment.party_name}</TableCell>
                <TableCell className="text-sm">{payment.payment_date}</TableCell>
                <TableCell className="text-sm">{payment.method}</TableCell>
                <TableCell className="text-right font-mono text-sm">
                  {Number(payment.amount).toFixed(2)}
                </TableCell>
                <TableCell>
                  <Badge variant={payment.status === "void" ? "destructive" : "outline"}>
                    {payment.status}
                  </Badge>
                </TableCell>
                <TableCell>
                  {payment.status === "recorded" && (
                    <Button size="sm" variant="ghost" onClick={() => voidPay.mutate(payment.id)}>
                      Void
                    </Button>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      <RecordPaymentDialog open={recordOpen} onOpenChange={setRecordOpen} onDone={invalidate} />
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
  const [direction, setDirection] = useState<"in" | "out">("in")
  const [partyId, setPartyId] = useState("")
  const [amount, setAmount] = useState("")
  const [method, setMethod] = useState("bank")
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  const contactsQuery = useQuery({
    queryKey: ["invoicing", "payment-contacts", direction, open],
    queryFn: () => invoicingApi.contacts(),
    enabled: open,
  })
  const contacts = (contactsQuery.data?.items ?? []).filter((c) =>
    direction === "in" ? c.is_customer : c.is_supplier,
  )

  const statementQuery = useQuery({
    queryKey: ["invoicing", "statement", partyId, open],
    queryFn: () => invoicingApi.statement(partyId),
    enabled: open && !!partyId,
  })
  const statement: Statement | undefined = statementQuery.data
  const openInvoices = (statement?.invoices ?? []).filter((i) => Number(i.balance) > 0)

  // FIFO auto-allocation preview (fold carries the running balance)
  const remaining = Number(amount) || 0
  const autoAllocations = openInvoices.reduce<
    { invoice: (typeof openInvoices)[number]; take: number }[]
  >((acc, invoice) => {
    const prev = acc.reduce((s, a) => s + a.take, 0)
    const take = Math.min(remaining - prev, Number(invoice.balance))
    return take > 0 ? [...acc, { invoice, take }] : acc
  }, [])
  const allocated = autoAllocations.reduce((sum, a) => sum + a.take, 0)

  const save = async () => {
    setSaving(true)
    setError(null)
    try {
      await invoicingApi.recordPayment({
        direction,
        party_id: partyId,
        amount,
        method,
        allocations: autoAllocations
          .filter((a) => a.take > 0)
          .map((a) => ({ invoice_id: a.invoice.invoice_id, amount: a.take.toFixed(2) })),
      })
      toast.success("Payment recorded")
      onDone()
      onOpenChange(false)
      setPartyId("")
      setAmount("")
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Record payment</DialogTitle>
        </DialogHeader>
        <div className="flex flex-col gap-4">
          <div className="grid grid-cols-2 gap-4">
            <div className="flex flex-col gap-2">
              <Label>Direction</Label>
              <Select
                value={direction}
                onValueChange={(v) => {
                  setDirection((v as "in" | "out") ?? "in")
                  setPartyId("")
                }}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="in">Money in (customer)</SelectItem>
                  <SelectItem value="out">Money out (supplier)</SelectItem>
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
              <Select value={partyId} onValueChange={(v) => setPartyId(v ?? "")}>
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

          {partyId && (
            <div className="rounded-md border p-3">
              <div className="mb-2 flex items-center justify-between text-sm">
                <span className="font-medium">Open invoices (FIFO allocation)</span>
                <span className="text-muted-foreground">
                  {Number(allocated).toFixed(2)} / {Number(amount || 0).toFixed(2)}
                </span>
              </div>
              {openInvoices.length === 0 && (
                <p className="text-sm text-muted-foreground">No open invoices for this party</p>
              )}
              <div className="flex flex-col gap-1">
                {autoAllocations
                  .filter((a) => a.take > 0)
                  .map(({ invoice, take }) => (
                    <div key={invoice.invoice_id} className="flex justify-between text-sm">
                      <span className="font-mono text-xs">
                        {invoice.number} (due {Number(invoice.balance).toFixed(2)})
                      </span>
                      <span className="font-mono">{take.toFixed(2)}</span>
                    </div>
                  ))}
              </div>
            </div>
          )}
          {error && <p className="text-xs text-destructive">{error}</p>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button disabled={!partyId || !amount || saving} onClick={() => void save()}>
            {saving ? "Saving…" : "Record payment"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
