import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { toast } from "sonner"

import {
  inventoryApi,
  type Adjustment,
  type Delivery,
  type Receipt,
} from "@/features/inventory/api"
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
import { formatMoney } from "@/lib/money"

function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : "Something went wrong"
}

const badgeFor = (status: string) =>
  status === "posted" ? "default" : status === "void" ? "destructive" : "outline"

export function StockPage() {
  const [warehouseId, setWarehouseId] = useState("all")
  const [lowOnly, setLowOnly] = useState(false)

  const warehousesQuery = useQuery({
    queryKey: ["inventory", "warehouses"],
    queryFn: () => inventoryApi.warehouses(),
  })
  const { data, isLoading } = useQuery({
    queryKey: ["inventory", "stock", warehouseId, lowOnly],
    queryFn: () =>
      inventoryApi.stock({
        warehouse_id: warehouseId === "all" ? undefined : warehouseId,
        low_only: lowOnly,
      }),
  })

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Stock on hand</h1>
          <p className="text-sm text-muted-foreground">Valued at moving-average cost</p>
        </div>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              className="size-4 accent-primary"
              checked={lowOnly}
              onChange={(e) => setLowOnly(e.target.checked)}
            />
            Low stock only
          </label>
          <Select value={warehouseId} onValueChange={(v) => setWarehouseId(v ?? "all")}>
            <SelectTrigger className="w-44">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All warehouses</SelectItem>
              {(warehousesQuery.data ?? []).map((w) => (
                <SelectItem key={w.id} value={w.id}>
                  {w.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      <div className="rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Product</TableHead>
              <TableHead>Warehouse</TableHead>
              <TableHead className="text-right">On hand</TableHead>
              <TableHead className="text-right">Avg cost</TableHead>
              <TableHead className="text-right">Value</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {isLoading && (
              <TableRow>
                <TableCell colSpan={5} className="text-center text-muted-foreground">
                  Loading…
                </TableCell>
              </TableRow>
            )}
            {!isLoading && data?.length === 0 && (
              <TableRow>
                <TableCell colSpan={5} className="text-center text-muted-foreground">
                  No stock movements yet — receive a purchase order to fill the shelves
                </TableCell>
              </TableRow>
            )}
            {(data ?? []).map((row) => (
              <TableRow key={`${row.product_id}-${row.warehouse_id}`}>
                <TableCell>
                  <div className="font-medium">{row.product_name}</div>
                  <div className="font-mono text-xs text-muted-foreground">{row.product_sku}</div>
                </TableCell>
                <TableCell className="text-sm">{row.warehouse_code}</TableCell>
                <TableCell className={`text-right font-mono text-sm ${row.is_low ? "font-semibold text-destructive" : ""}`}>
                  {Number(row.qty_on_hand).toFixed(2)}
                  {row.is_low && " ⚠"}
                </TableCell>
                <TableCell className="text-right font-mono text-sm">
                  {Number(row.avg_cost).toFixed(4)}
                </TableCell>
                <TableCell className="text-right font-mono text-sm">
                  {formatMoney(row.stock_value)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  )
}

export function ReceiptsPage() {
  const queryClient = useQueryClient()
  const [createOpen, setCreateOpen] = useState(false)
  const { data, isLoading, refetch } = useQuery({
    queryKey: ["inventory", "receipts", {}],
    queryFn: () => inventoryApi.receipts({ limit: 50 }),
  })
  const invalidate = () => void queryClient.invalidateQueries({ queryKey: ["inventory"] })

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Receipts</h1>
          <p className="text-sm text-muted-foreground">Goods in — posting raises stock and updates average cost</p>
        </div>
        <Button onClick={() => setCreateOpen(true)}>New receipt</Button>
      </div>
      <DocTable
        isLoading={isLoading}
        items={(data?.items ?? []) as DocRow[]}
        onAction={async (doc, action) => {
          try {
            if (action === "post") await inventoryApi.postReceipt(doc.id)
            if (action === "void") await inventoryApi.voidReceipt(doc.id)
            toast.success(`Receipt ${action}ed`)
            invalidate()
            void refetch()
          } catch (err) {
            toast.error(errorMessage(err))
          }
        }}
      />
      <CreateFromOrderDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        kind="receipt"
        onDone={invalidate}
      />
    </div>
  )
}

export function DeliveriesPage() {
  const queryClient = useQueryClient()
  const [createOpen, setCreateOpen] = useState(false)
  const { data, isLoading, refetch } = useQuery({
    queryKey: ["inventory", "deliveries", {}],
    queryFn: () => inventoryApi.deliveries({ limit: 50 }),
  })
  const invalidate = () => void queryClient.invalidateQueries({ queryKey: ["inventory"] })

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Deliveries</h1>
          <p className="text-sm text-muted-foreground">Goods out — posting reduces stock and captures COGS</p>
        </div>
        <Button onClick={() => setCreateOpen(true)}>New delivery</Button>
      </div>
      <DocTable
        isLoading={isLoading}
        items={(data?.items ?? []) as DocRow[]}
        onAction={async (doc, action) => {
          try {
            if (action === "post") await inventoryApi.postDelivery(doc.id)
            if (action === "void") await inventoryApi.voidDelivery(doc.id)
            toast.success(`Delivery ${action}ed`)
            invalidate()
            void refetch()
          } catch (err) {
            toast.error(errorMessage(err))
          }
        }}
      />
      <CreateFromOrderDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        kind="delivery"
        onDone={invalidate}
      />
    </div>
  )
}

export function AdjustmentsPage() {
  const queryClient = useQueryClient()
  const [createOpen, setCreateOpen] = useState(false)
  const { data, isLoading, refetch } = useQuery({
    queryKey: ["inventory", "adjustments", {}],
    queryFn: () => inventoryApi.adjustments({ limit: 50 }),
  })
  const invalidate = () => void queryClient.invalidateQueries({ queryKey: ["inventory"] })

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Adjustments</h1>
          <p className="text-sm text-muted-foreground">Corrections, damages, count differences</p>
        </div>
        <Button onClick={() => setCreateOpen(true)}>New adjustment</Button>
      </div>
      <DocTable
        isLoading={isLoading}
        allowVoid={false}
        items={(data?.items ?? []) as DocRow[]}
        onAction={async (doc) => {
          try {
            await inventoryApi.postAdjustment(doc.id)
            toast.success("Adjustment posted")
            invalidate()
            void refetch()
          } catch (err) {
            toast.error(errorMessage(err))
          }
        }}
      />
      <CreateAdjustmentDialog open={createOpen} onOpenChange={setCreateOpen} onDone={invalidate} />
    </div>
  )
}

type DocRow = (Receipt | Delivery | Adjustment) & {
  source_number?: string | null
  reason?: string | null
}

function DocTable({
  isLoading,
  items,
  onAction,
  allowVoid = true,
}: {
  isLoading: boolean
  items: DocRow[]
  onAction: (doc: DocRow, action: "post" | "void") => void
  allowVoid?: boolean
}) {
  return (
    <div className="rounded-lg border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Number</TableHead>
            <TableHead>Source</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Lines</TableHead>
            <TableHead className="w-40" />
          </TableRow>
        </TableHeader>
        <TableBody>
          {isLoading && (
            <TableRow>
              <TableCell colSpan={5} className="text-center text-muted-foreground">
                Loading…
              </TableCell>
            </TableRow>
          )}
          {!isLoading && items.length === 0 && (
            <TableRow>
              <TableCell colSpan={5} className="text-center text-muted-foreground">
                Nothing here yet
              </TableCell>
            </TableRow>
          )}
          {items.map((doc) => (
            <TableRow key={doc.id}>
              <TableCell className="font-mono text-xs">{doc.number}</TableCell>
              <TableCell className="font-mono text-xs text-muted-foreground">
                {doc.source_number ?? "—"}
                {doc.reason ? ` (${doc.reason})` : ""}
              </TableCell>
              <TableCell>
                <Badge variant={badgeFor(doc.status)}>{doc.status}</Badge>
              </TableCell>
              <TableCell className="text-sm">{doc.lines.length}</TableCell>
              <TableCell>
                <div className="flex gap-1">
                  {doc.status === "draft" && (
                    <Button size="sm" variant="outline" onClick={() => onAction(doc, "post")}>
                      Post
                    </Button>
                  )}
                  {doc.status === "posted" && allowVoid && (
                    <Button size="sm" variant="ghost" onClick={() => onAction(doc, "void")}>
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
  )
}

function CreateFromOrderDialog({
  open,
  onOpenChange,
  kind,
  onDone,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  kind: "receipt" | "delivery"
  onDone: () => void
}) {
  const [orderId, setOrderId] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  const ordersQuery = useQuery({
    queryKey: ["inventory", "source-orders", kind, open],
    queryFn: () => (kind === "receipt" ? inventoryApi.purchaseOrders() : inventoryApi.salesOrders()),
    enabled: open,
  })
  // Goods may still be outstanding on partly moved or already-invoiced orders.
  const movable = new Set(["confirmed", "delivered", "received", "invoiced"])
  const orders = (ordersQuery.data?.items ?? []).filter((o) => movable.has(o.status))

  const create = async () => {
    setSaving(true)
    setError(null)
    try {
      if (kind === "receipt") {
        await inventoryApi.createReceipt({ source_po_id: orderId })
      } else {
        await inventoryApi.createDelivery({ source_so_id: orderId })
      }
      toast.success(`${kind === "receipt" ? "Receipt" : "Delivery"} created from order`)
      onDone()
      onOpenChange(false)
      setOrderId("")
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
          <DialogTitle>
            New {kind} from a confirmed {kind === "receipt" ? "purchase" : "sales"} order
          </DialogTitle>
        </DialogHeader>
        <div className="flex flex-col gap-4">
          <div className="flex flex-col gap-2">
            <Label>Order</Label>
            <Select value={orderId} onValueChange={(v) => setOrderId(v ?? "")}>
              <SelectTrigger>
                <SelectValue placeholder="Select order…" />
              </SelectTrigger>
              <SelectContent>
                {orders.length === 0 && (
                  <div className="px-3 py-2 text-sm text-muted-foreground">
                    No confirmed orders waiting
                  </div>
                )}
                {orders.map((order) => (
                  <SelectItem key={order.id} value={order.id}>
                    {order.number} — {order.customer_name ?? order.supplier_name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          {error && <p className="text-xs text-destructive">{error}</p>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button disabled={!orderId || saving} onClick={() => void create()}>
            {saving ? "Creating…" : "Create"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function CreateAdjustmentDialog({
  open,
  onOpenChange,
  onDone,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onDone: () => void
}) {
  const queryClient = useQueryClient()
  const [reason, setReason] = useState("stock_correction")
  const [productId, setProductId] = useState("")
  const [qty, setQty] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  const productsQuery = useQuery({
    queryKey: ["inventory", "products"],
    queryFn: () => inventoryApi.products(),
    enabled: open,
  })
  const products = productsQuery.data?.items ?? []

  const create = async () => {
    setSaving(true)
    setError(null)
    try {
      const created = await inventoryApi.createAdjustment({
        reason,
        lines: [{ product_id: productId, qty }],
      })
      await inventoryApi.postAdjustment(created.id)
      toast.success("Adjustment posted")
      void queryClient.invalidateQueries({ queryKey: ["inventory"] })
      onDone()
      onOpenChange(false)
      setProductId("")
      setQty("")
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
          <DialogTitle>New adjustment</DialogTitle>
        </DialogHeader>
        <div className="flex flex-col gap-4">
          <div className="flex flex-col gap-2">
            <Label>Reason</Label>
            <Select value={reason} onValueChange={(v) => setReason(v ?? "stock_correction")}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="stock_correction">Stock correction</SelectItem>
                <SelectItem value="damage">Damage</SelectItem>
                <SelectItem value="count">Count difference</SelectItem>
                <SelectItem value="other">Other</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="flex flex-col gap-2">
            <Label>Product</Label>
            <Select value={productId} onValueChange={(v) => setProductId(v ?? "")}>
              <SelectTrigger>
                <SelectValue placeholder="Select product…" />
              </SelectTrigger>
              <SelectContent>
                {products.map((p) => (
                  <SelectItem key={p.id} value={p.id}>
                    {p.name} ({p.sku})
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor="adj-qty">Quantity (negative for shrinkage)</Label>
            <Input id="adj-qty" type="number" step="0.0001" value={qty} onChange={(e) => setQty(e.target.value)} />
          </div>
          {error && <p className="text-xs text-destructive">{error}</p>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button disabled={!productId || !qty || saving} onClick={() => void create()}>
            {saving ? "Posting…" : "Create & post"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
