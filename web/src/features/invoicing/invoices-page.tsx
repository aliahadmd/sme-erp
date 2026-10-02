import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { toast } from "sonner"

import { invoicingApi, type Invoice as InvoiceT } from "@/features/invoicing/api"
import { CurrencySelect } from "@/components/currency-select"
import { formatMoney } from "@/lib/money"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
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
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { ApiError } from "@/lib/api/client"

function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : "Something went wrong"
}

/** Orders that can still be invoiced (partly delivered/invoiced included). */
const INVOICEABLE = new Set(["confirmed", "delivered", "received", "invoiced"])

const isCredit = (invoice: InvoiceT) => invoice.invoice_type.endsWith("_credit")

const statusVariant = (status: string) =>
  status === "paid"
    ? "default"
    : status === "void" || status === "cancelled"
      ? "destructive"
      : status === "partial"
        ? "secondary"
        : "outline"

export function InvoicesPage({ side }: { side: "ar" | "ap" }) {
  const queryClient = useQueryClient()
  const [createOpen, setCreateOpen] = useState(false)

  const { data, isLoading, refetch } = useQuery({
    queryKey: ["invoicing", "invoices", side],
    queryFn: () => invoicingApi.invoices({ side, limit: 50 }),
  })

  const invalidate = () => void queryClient.invalidateQueries({ queryKey: ["invoicing"] })

  const post = useMutation({
    mutationFn: (invoice: InvoiceT) => invoicingApi.postInvoice(invoice.id),
    onSuccess: () => {
      toast.success("Invoice posted — journal entry created")
      invalidate()
      void refetch()
    },
    onError: (err) => toast.error(errorMessage(err)),
  })
  const voidInv = useMutation({
    mutationFn: (invoice: InvoiceT) => invoicingApi.voidInvoice(invoice.id),
    onSuccess: () => {
      toast.success("Voided — reversal entry posted")
      invalidate()
      void refetch()
    },
    onError: (err) => toast.error(errorMessage(err)),
  })
  const deleteDraft = useMutation({
    mutationFn: (invoice: InvoiceT) => invoicingApi.deleteDraft(invoice.id),
    onSuccess: () => {
      toast.success("Draft deleted")
      invalidate()
      void refetch()
    },
    onError: (err) => toast.error(errorMessage(err)),
  })
  const email = useMutation({
    mutationFn: (invoice: InvoiceT) => invoicingApi.emailInvoice(invoice.id),
    onSuccess: (result) => toast.success(`Email queued to ${result.to}`),
    onError: (err) => toast.error(errorMessage(err)),
  })
  const credit = useMutation({
    mutationFn: (invoice: InvoiceT) =>
      invoicingApi.createInvoice({
        invoice_type: invoice.invoice_type === "ap" ? "ap_credit" : "ar_credit",
        party_id: invoice.party_id ?? "",
        original_invoice_id: invoice.id,
      }),
    onSuccess: () => {
      toast.success("Draft credit note created (full amount) — review and post it")
      invalidate()
      void refetch()
    },
    onError: (err) => toast.error(errorMessage(err)),
  })

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">
            {side === "ar" ? "Customer invoices" : "Supplier bills"}
          </h1>
          <p className="text-sm text-muted-foreground">
            Posting is final — corrections via void (reversal entries are automatic)
          </p>
        </div>
        <Button onClick={() => setCreateOpen(true)}>New {side === "ar" ? "invoice" : "bill"}</Button>
      </div>

      <div className="rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Number</TableHead>
              <TableHead>Party</TableHead>
              <TableHead>Source</TableHead>
              <TableHead>Due</TableHead>
              <TableHead className="text-right">Total</TableHead>
              <TableHead className="text-right">Open</TableHead>
              <TableHead>Status</TableHead>
              <TableHead />
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
                  No invoices yet
                </TableCell>
              </TableRow>
            )}
            {data?.items.map((invoice) => {
              const overdue =
                ["posted", "partial"].includes(invoice.status) &&
                !!invoice.due_date &&
                new Date(invoice.due_date) < new Date()
              return (
                <TableRow key={invoice.id}>
                  <TableCell className="font-mono text-xs">
                    {invoice.number ?? "draft"}
                    {isCredit(invoice) && (
                      <Badge variant="secondary" className="ml-1.5">
                        credit
                      </Badge>
                    )}
                  </TableCell>
                  <TableCell>{invoice.party_name}</TableCell>
                  <TableCell className="font-mono text-xs text-muted-foreground">
                    {invoice.source_number ?? "—"}
                  </TableCell>
                  <TableCell className="text-sm">
                    {invoice.due_date}
                    {overdue && <span className="ml-1 text-destructive">⚠</span>}
                  </TableCell>
                  <TableCell className="text-right font-mono text-sm">
                    {isCredit(invoice) ? "−" : ""}
                    {formatMoney(invoice.total)} {invoice.currency}
                  </TableCell>
                  <TableCell className="text-right font-mono text-sm">
                    {invoice.status === "draft" || invoice.status === "void"
                      ? "—"
                      : formatMoney(invoice.open_balance)}
                  </TableCell>
                  <TableCell>
                    <Badge variant={statusVariant(invoice.status)}>{invoice.status}</Badge>
                  </TableCell>
                  <TableCell>
                    <div className="flex flex-wrap justify-end gap-1">
                      {invoice.status === "draft" && (
                        <>
                          <Button size="sm" variant="outline" onClick={() => post.mutate(invoice)}>
                            Post
                          </Button>
                          <Button size="sm" variant="ghost" onClick={() => deleteDraft.mutate(invoice)}>
                            Delete
                          </Button>
                        </>
                      )}
                      {!isCredit(invoice) && ["posted", "partial", "paid"].includes(invoice.status) && (
                        <Button size="sm" variant="ghost" onClick={() => credit.mutate(invoice)}>
                          Credit note
                        </Button>
                      )}
                      {side === "ar" && invoice.status !== "draft" && invoice.status !== "void" && (
                        <Button size="sm" variant="ghost" onClick={() => email.mutate(invoice)}>
                          Email
                        </Button>
                      )}
                      {(invoice.status === "posted" ||
                        (isCredit(invoice) && ["partial", "paid"].includes(invoice.status))) && (
                        <Button size="sm" variant="ghost" onClick={() => voidInv.mutate(invoice)}>
                          Void
                        </Button>
                      )}
                    </div>
                  </TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      </div>

      <CreateInvoiceDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        side={side}
        onDone={invalidate}
      />
    </div>
  )
}

function CreateInvoiceDialog({
  open,
  onOpenChange,
  side,
  onDone,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  side: "ar" | "ap"
  onDone: () => void
}) {
  const [partyId, setPartyId] = useState("")
  const [orderId, setOrderId] = useState("standalone")
  const [description, setDescription] = useState("")
  const [qty, setQty] = useState("1")
  const [price, setPrice] = useState("")
  const [currency, setCurrency] = useState<string | undefined>(undefined)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  const contactsQuery = useQuery({
    queryKey: ["invoicing", "contacts", side],
    queryFn: () => invoicingApi.contacts(),
    enabled: open,
  })
  const ordersQuery = useQuery({
    queryKey: ["invoicing", "orders", side],
    queryFn: () => (side === "ar" ? invoicingApi.salesOrders() : invoicingApi.purchaseOrders()),
    enabled: open,
  })
  const orders = (ordersQuery.data?.items ?? []).filter((o) => INVOICEABLE.has(o.status))
  const contacts = (contactsQuery.data?.items ?? []).filter((c) =>
    side === "ar" ? c.is_customer : c.is_supplier,
  )

  const create = async () => {
    setSaving(true)
    setError(null)
    try {
      const body: Parameters<typeof invoicingApi.createInvoice>[0] = {
        invoice_type: side,
        party_id: partyId,
      }
      if (orderId !== "standalone") {
        body.source_order_id = orderId
      } else {
        body.lines = [{ description: description || "Item", qty, unit_price: price || "0" }]
        body.currency = currency
      }
      await invoicingApi.createInvoice(body)
      toast.success("Draft invoice created — post it when ready")
      onDone()
      onOpenChange(false)
      setPartyId("")
      setOrderId("standalone")
      setDescription("")
      setQty("1")
      setPrice("")
      setCurrency(undefined)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>New {side === "ar" ? "customer invoice" : "supplier bill"}</DialogTitle>
        </DialogHeader>
        <div className="flex flex-col gap-4">
          <div className="flex flex-col gap-2">
            <Label>{side === "ar" ? "Customer" : "Supplier"} *</Label>
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
            <Label>From order (optional)</Label>
            <Select value={orderId} onValueChange={(v) => setOrderId(v ?? "standalone")}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="standalone">— standalone lines —</SelectItem>
                {orders.map((o) => (
                  <SelectItem key={o.id} value={o.id}>
                    {o.number} — {o.customer_name ?? o.supplier_name} ({formatMoney(o.total)}{" "}
                    {o.currency}, {o.status})
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          {orderId === "standalone" && (
            <div className="flex flex-col gap-3 rounded-md border p-3">
              <div className="flex flex-col gap-2">
                <Label htmlFor="inv-desc">Description</Label>
                <Input id="inv-desc" value={description} onChange={(e) => setDescription(e.target.value)} />
              </div>
              <div className="flex flex-col gap-2">
                <Label htmlFor="inv-currency">Currency</Label>
                <CurrencySelect id="inv-currency" value={currency} onChange={setCurrency} />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div className="flex flex-col gap-2">
                  <Label htmlFor="inv-qty">Qty</Label>
                  <Input id="inv-qty" type="number" step="0.0001" value={qty} onChange={(e) => setQty(e.target.value)} />
                </div>
                <div className="flex flex-col gap-2">
                  <Label htmlFor="inv-price">Unit price</Label>
                  <Input id="inv-price" type="number" step="0.01" value={price} onChange={(e) => setPrice(e.target.value)} />
                </div>
              </div>
            </div>
          )}
          {error && <p className="text-xs text-destructive">{error}</p>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button disabled={!partyId || saving} onClick={() => void create()}>
            {saving ? "Creating…" : "Create draft"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
