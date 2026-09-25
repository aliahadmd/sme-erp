import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Trash2 } from "lucide-react"
import { useState } from "react"
import { Link, useNavigate, useParams } from "react-router"
import { toast } from "sonner"

import {
  ordersApi,
  type OrderLine,
  type OrderModule,
} from "@/features/documents/api"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Separator } from "@/components/ui/separator"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { ApiError } from "@/lib/api/client"
import { queryKeys } from "@/lib/query-keys"

function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : "Something went wrong"
}

function money(value: number): string {
  // banker-style 2dp rounding to mirror the server
  const rounded = Math.round(value * 100) / 100
  return rounded.toFixed(2)
}

function lineBase(line: OrderLine): number {
  const qty = Number(line.qty) || 0
  const price = Number(line.unit_price) || 0
  const disc = Number(line.discount_pct) || 0
  return Number(money(qty * price * (1 - disc / 100)))
}

function lineTax(line: OrderLine, rate: number): number {
  return Number(money(lineBase(line) * (rate / 100)))
}

function blankLine(): OrderLine {
  return {
    product_id: null,
    qty: "1",
    unit_price: "0",
    discount_pct: "0",
    tax_id: null,
  }
}

export function OrderEditorPage({ module }: { module: OrderModule }) {
  const { id } = useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const isNew = !id

  const orderQuery = useQuery({
    queryKey: queryKeys.salesOrder(id ?? "new"),
    queryFn: () => ordersApi.get(module, id!),
    enabled: !!id,
  })

  const contactsQuery = useQuery({ queryKey: ["pickers", "contacts"], queryFn: () => ordersApi.contacts() })
  const productsQuery = useQuery({ queryKey: ["pickers", "products"], queryFn: () => ordersApi.products() })
  const taxesQuery = useQuery({ queryKey: ["pickers", "taxes"], queryFn: () => ordersApi.taxes() })

  // ---- editor state
  const [partyId, setPartyId] = useState<string>("")
  const [orderDate, setOrderDate] = useState(new Date().toISOString().slice(0, 10))
  const [expectedDate, setExpectedDate] = useState("")
  const [notes, setNotes] = useState("")
  const [lines, setLines] = useState<OrderLine[]>([blankLine()])
  const [error, setError] = useState<string | null>(null)
  const [initializedFrom, setInitializedFrom] = useState<string | null>(null)

  const serverOrder = orderQuery.data
  if (serverOrder && initializedFrom !== serverOrder.id) {
    setInitializedFrom(serverOrder.id)
    setPartyId(
      (serverOrder.customer_id ?? serverOrder.supplier_id ?? "") as string,
    )
    setOrderDate(serverOrder.order_date)
    setExpectedDate(serverOrder.expected_date ?? "")
    setNotes(serverOrder.notes ?? "")
    setLines(serverOrder.lines.map((l) => ({ ...l, qty: String(l.qty), unit_price: String(l.unit_price), discount_pct: String(l.discount_pct ?? "0") })))
  }

  const taxRateFor = (line: OrderLine): number => {
    if (line.tax_rate_pct !== undefined) return Number(line.tax_rate_pct)
    const tax = (taxesQuery.data ?? []).find((t) => t.id === line.tax_id)
    return tax ? Number(tax.rate_pct) : 0
  }

  const totals = lines.reduce(
    (acc, line) => {
      const base = lineBase(line)
      const tax = lineTax(line, taxRateFor(line))
      const gross = Number(money((Number(line.qty) || 0) * (Number(line.unit_price) || 0)))
      return {
        subtotal: acc.subtotal + gross,
        discount: acc.discount + (gross - base),
        tax: acc.tax + tax,
        total: acc.total + base + tax,
      }
    },
    { subtotal: 0, discount: 0, tax: 0, total: 0 },
  )

  const setLine = (index: number, patch: Partial<OrderLine>) =>
    setLines((prev) => prev.map((line, i) => (i === index ? { ...line, ...patch } : line)))

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: queryKeys.salesOrders({ module }) })
  }

  const save = useMutation({
    mutationFn: async () => {
      const body = {
        order_date: orderDate,
        expected_date: expectedDate || null,
        notes: notes || null,
        lines: lines.map((line) => ({
          product_id: line.product_id,
          description: line.description ?? null,
          qty: line.qty,
          unit_price: line.unit_price === null ? null : line.unit_price,
          discount_pct: line.discount_pct,
          tax_id: line.tax_id,
        })),
      }
      if (isNew) {
        return ordersApi.create(module, { [module === "sales" ? "customer_id" : "supplier_id"]: partyId, ...body })
      }
      return ordersApi.update(module, id!, { [module === "sales" ? "customer_id" : "supplier_id"]: partyId, ...body })
    },
    onSuccess: (saved) => {
      toast.success("Saved")
      invalidate()
      if (isNew) navigate(`/${module}/${saved.id}`, { replace: true })
    },
    onError: (err) => setError(errorMessage(err)),
  })

  const confirm = useMutation({
    mutationFn: () => ordersApi.confirm(module, id!),
    onSuccess: () => {
      toast.success("Confirmed")
      invalidate()
      void orderQuery.refetch()
    },
    onError: (err) => toast.error(errorMessage(err)),
  })

  const cancel = useMutation({
    mutationFn: () => ordersApi.cancel(module, id!),
    onSuccess: () => {
      toast.success("Cancelled")
      invalidate()
      void orderQuery.refetch()
    },
    onError: (err) => toast.error(errorMessage(err)),
  })

  const status = serverOrder?.status ?? "draft"
  const editable = status === "draft"
  const busy = save.isPending || confirm.isPending || cancel.isPending

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-3">
          <h1 className="text-xl font-semibold tracking-tight">
            {isNew ? `New ${module === "sales" ? "sales" : "purchase"} order` : serverOrder?.number ?? "…"}
          </h1>
          {!isNew && <Badge variant={status === "cancelled" ? "destructive" : status === "draft" ? "outline" : "default"}>{status}</Badge>}
        </div>
        <div className="flex gap-2">
          <Link to={`/${module}`}>
            <Button variant="ghost">Back to list</Button>
          </Link>
          {editable && (
            <Button disabled={busy || !partyId} onClick={() => save.mutate()}>
              {save.isPending ? "Saving…" : "Save draft"}
            </Button>
          )}
          {status === "draft" && !isNew && (
            <Button disabled={busy} onClick={() => confirm.mutate()}>
              {confirm.isPending ? "Confirming…" : "Confirm"}
            </Button>
          )}
          {(status === "draft" || status === "confirmed") && !isNew && (
            <Button variant="destructive" disabled={busy} onClick={() => cancel.mutate()}>
              Cancel order
            </Button>
          )}
        </div>
      </div>

      {error && <p className="rounded-md bg-destructive/10 px-3 py-2 text-xs text-destructive">{error}</p>}

      {/* header */}
      <div className="grid gap-4 rounded-lg border p-4 sm:grid-cols-2 lg:grid-cols-4">
        <div className="flex flex-col gap-2">
          <Label>{module === "sales" ? "Customer" : "Supplier"} *</Label>
          <Select
            value={partyId}
            disabled={!editable}
            onValueChange={(v) => setPartyId(v ?? "")}
            items={{
              "": "Select…",
              ...Object.fromEntries(
                (contactsQuery.data?.items ?? []).map((c) => [c.id, c.name]),
              ),
            }}
          >
            <SelectTrigger>
              <SelectValue placeholder="Select…" />
            </SelectTrigger>
            <SelectContent>
              {(contactsQuery.data?.items ?? []).map((contact) => (
                <SelectItem key={contact.id} value={contact.id}>
                  {contact.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="flex flex-col gap-2">
          <Label>Order date</Label>
          <Input type="date" value={orderDate} disabled={!editable} onChange={(e) => setOrderDate(e.target.value)} />
        </div>
        <div className="flex flex-col gap-2">
          <Label>Expected date</Label>
          <Input type="date" value={expectedDate} disabled={!editable} onChange={(e) => setExpectedDate(e.target.value)} />
        </div>
        <div className="flex flex-col gap-2">
          <Label>Notes</Label>
          <Input value={notes} disabled={!editable} onChange={(e) => setNotes(e.target.value)} />
        </div>
      </div>

      {/* lines */}
      <div className="rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-64">Product</TableHead>
              <TableHead className="w-24">Qty</TableHead>
              <TableHead className="w-28">Unit price</TableHead>
              <TableHead className="w-24">Disc %</TableHead>
              <TableHead className="w-40">Tax</TableHead>
              <TableHead className="text-right">Total</TableHead>
              <TableHead className="w-10" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {lines.map((line, index) => (
              <TableRow key={index}>
                <TableCell>
                  <Select
                    value={line.product_id ?? "none"}
                    disabled={!editable}
                    items={{
                      none: "—",
                      ...Object.fromEntries(
                        (productsQuery.data?.items ?? []).map((p) => [
                          p.id,
                          `${p.name} (${p.sku})`,
                        ]),
                      ),
                    }}
                    onValueChange={(v) => {
                      const product = (productsQuery.data?.items ?? []).find((p) => p.id === v)
                      setLine(index, {
                        product_id: v === "none" ? null : v,
                        unit_price:
                          line.unit_price && Number(line.unit_price) > 0
                            ? line.unit_price
                            : product
                              ? String(module === "sales" ? Number(product.sale_price) : Number(product.cost_price))
                              : line.unit_price,
                        tax_id:
                          line.tax_id ??
                          (module === "sales" ? product?.sale_tax_id : product?.purchase_tax_id) ??
                          null,
                      })
                    }}
                  >
                    <SelectTrigger>
                      <SelectValue placeholder="None (free text)" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="none">—</SelectItem>
                      {(productsQuery.data?.items ?? []).map((product) => (
                        <SelectItem key={product.id} value={product.id}>
                          {product.name} ({product.sku})
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </TableCell>
                <TableCell>
                  <Input
                    type="number"
                    step="0.0001"
                    className="text-right"
                    disabled={!editable}
                    value={line.qty}
                    onChange={(e) => setLine(index, { qty: e.target.value })}
                  />
                </TableCell>
                <TableCell>
                  <Input
                    type="number"
                    step="0.000001"
                    className="text-right"
                    disabled={!editable}
                    value={line.unit_price ?? ""}
                    onChange={(e) => setLine(index, { unit_price: e.target.value })}
                  />
                </TableCell>
                <TableCell>
                  <Input
                    type="number"
                    step="0.01"
                    className="text-right"
                    disabled={!editable}
                    value={line.discount_pct}
                    onChange={(e) => setLine(index, { discount_pct: e.target.value })}
                  />
                </TableCell>
                <TableCell>
                  <Select
                    value={line.tax_id ?? "none"}
                    disabled={!editable}
                    onValueChange={(v) => setLine(index, { tax_id: v === "none" ? null : v })}
                    items={{
                      none: "No tax",
                      ...Object.fromEntries(
                        (taxesQuery.data ?? []).map((t) => [t.id, t.name]),
                      ),
                    }}
                  >
                    <SelectTrigger>
                      <SelectValue placeholder="No tax" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="none">No tax</SelectItem>
                      {(taxesQuery.data ?? []).map((tax) => (
                        <SelectItem key={tax.id} value={tax.id}>
                          {tax.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </TableCell>
                <TableCell className="text-right font-mono text-sm">
                  {money(lineBase(line) + lineTax(line, taxRateFor(line)))}
                </TableCell>
                <TableCell>
                  {editable && (
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label="Remove line"
                      onClick={() => setLines((prev) => prev.filter((_, i) => i !== index))}
                    >
                      <Trash2 className="size-4" />
                    </Button>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        {editable && (
          <div className="border-t p-2">
            <Button variant="outline" size="sm" onClick={() => setLines((prev) => [...prev, blankLine()])}>
              Add line
            </Button>
          </div>
        )}
      </div>

      {/* totals */}
      <div className="flex justify-end">
        <div className="w-64 rounded-lg border p-4 text-sm">
          <div className="flex justify-between py-0.5">
            <span className="text-muted-foreground">Subtotal</span>
            <span className="font-mono">{money(totals.subtotal)}</span>
          </div>
          <div className="flex justify-between py-0.5">
            <span className="text-muted-foreground">Discount</span>
            <span className="font-mono">-{money(totals.discount)}</span>
          </div>
          <div className="flex justify-between py-0.5">
            <span className="text-muted-foreground">Tax</span>
            <span className="font-mono">{money(totals.tax)}</span>
          </div>
          <Separator className="my-2" />
          <div className="flex justify-between text-base font-semibold">
            <span>Total</span>
            <span className="font-mono">{money(totals.total)}</span>
          </div>
        </div>
      </div>
    </div>
  )
}

export function OrderEditorRoute({ module }: { module: OrderModule }) {
  const { id } = useParams()
  return <OrderEditorPage key={id} module={module} />
}
