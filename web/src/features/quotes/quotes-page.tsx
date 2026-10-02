import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Plus } from "lucide-react"
import { useState } from "react"
import { toast } from "sonner"

import { quotesApi, type Quotation } from "@/features/quotes/api"
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
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { ApiError } from "@/lib/api/client"
import { formatMoney } from "@/lib/money"

function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : "Something went wrong"
}

const statusVariant = (status: string) =>
  status === "accepted" || status === "converted"
    ? "default"
    : status === "expired" || status === "rejected" || status === "cancelled"
      ? "destructive"
      : status === "sent"
        ? "secondary"
        : "outline"

export function QuotesPage() {
  const queryClient = useQueryClient()
  const invalidate = () => void queryClient.invalidateQueries({ queryKey: ["quotes"] })
  const [status, setStatus] = useState("all")
  const [creating, setCreating] = useState(false)

  const { data, isLoading, refetch } = useQuery({
    queryKey: ["quotes", { status }],
    queryFn: () => quotesApi.list({ status: status === "all" ? undefined : status, limit: 50 }),
  })

  const act = async (quote: Quotation, action: "send" | "accept" | "reject" | "cancel" | "convert") => {
    try {
      const fn = {
        send: quotesApi.send,
        accept: quotesApi.accept,
        reject: quotesApi.reject,
        cancel: quotesApi.cancel,
        convert: quotesApi.convert,
      }[action]
      const result = await fn(quote.id)
      if (action === "convert") {
        toast.success(`Converted to ${(result as { order_number: string }).order_number}`)
      } else {
        toast.success(`Quotation ${action === "send" ? "sent" : `${action}ed`}`)
      }
      invalidate()
      void refetch()
    } catch (err) {
      toast.error(errorMessage(err))
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Quotations</h1>
          <p className="text-sm text-muted-foreground">
            Accepted quotes convert into draft sales orders — exactly once
          </p>
        </div>
        <Button onClick={() => setCreating(true)}>
          <Plus className="size-4" /> New quotation
        </Button>
      </div>

      <Tabs value={status} onValueChange={setStatus}>
        <TabsList>
          {["all", "draft", "sent", "accepted", "converted", "expired"].map((s) => (
            <TabsTrigger key={s} value={s}>
              {s}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>

      <div className="rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Number</TableHead>
              <TableHead>Customer</TableHead>
              <TableHead>Valid until</TableHead>
              <TableHead>Status</TableHead>
              <TableHead className="text-right">Total</TableHead>
              <TableHead className="w-56" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {isLoading && (
              <TableRow>
                <TableCell colSpan={6} className="text-center text-muted-foreground">
                  Loading…
                </TableCell>
              </TableRow>
            )}
            {!isLoading && data?.items.length === 0 && (
              <TableRow>
                <TableCell colSpan={6} className="text-center text-muted-foreground">
                  No quotations yet
                </TableCell>
              </TableRow>
            )}
            {data?.items.map((quote) => (
              <TableRow key={quote.id}>
                <TableCell className="font-mono text-xs">{quote.number}</TableCell>
                <TableCell>{quote.customer_name}</TableCell>
                <TableCell className="text-sm">
                  {quote.valid_until ?? "—"}
                  {quote.status === "sent" && quote.valid_until && new Date(quote.valid_until) < new Date() && (
                    <Badge variant="destructive" className="ml-1">
                      expired
                    </Badge>
                  )}
                </TableCell>
                <TableCell>
                  <Badge variant={statusVariant(quote.status)}>{quote.status}</Badge>
                </TableCell>
                <TableCell className="text-right font-mono text-sm">
                  {formatMoney(quote.total)}
                </TableCell>
                <TableCell>
                  <div className="flex justify-end gap-1">
                    {quote.status === "draft" && (
                      <Button size="sm" variant="outline" onClick={() => act(quote, "send")}>
                        Send
                      </Button>
                    )}
                    {quote.status === "sent" && (
                      <Button size="sm" onClick={() => act(quote, "accept")}>
                        Accept
                      </Button>
                    )}
                    {quote.status === "accepted" && (
                      <Button size="sm" onClick={() => act(quote, "convert")}>
                        Convert
                      </Button>
                    )}
                    {["draft", "sent", "accepted"].includes(quote.status) && (
                      <Button size="sm" variant="ghost" onClick={() => act(quote, "cancel")}>
                        Cancel
                      </Button>
                    )}
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      <CreateQuoteDialog open={creating} onOpenChange={setCreating} onDone={invalidate} />
    </div>
  )
}

function CreateQuoteDialog({
  open,
  onOpenChange,
  onDone,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onDone: () => void
}) {
  const [partyId, setPartyId] = useState("")
  const [validUntil, setValidUntil] = useState("")
  const [description, setDescription] = useState("")
  const [qty, setQty] = useState("1")
  const [price, setPrice] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  const contactsQuery = useQuery({
    queryKey: ["quotes", "contacts", open],
    queryFn: () => quotesApi.contacts(),
    enabled: open,
  })

  const create = async () => {
    setSaving(true)
    setError(null)
    try {
      const quote = await quotesApi.create({
        customer_id: partyId,
        valid_until: validUntil || null,
        lines: [{ product_id: null, description: description || "Item", qty, unit_price: price || "0" }],
      })
      // send right away — drafts need an explicit send action otherwise
      await quotesApi.send(quote.id)
      toast.success("Quotation created and sent")
      onDone()
      onOpenChange(false)
      setPartyId("")
      setDescription("")
      setQty("1")
      setPrice("")
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
          <DialogTitle>New quotation</DialogTitle>
        </DialogHeader>
        <div className="flex flex-col gap-4">
          <div className="flex flex-col gap-2">
            <Label>Customer *</Label>
            <Select value={partyId} onValueChange={(v) => setPartyId(v ?? "")}>
              <SelectTrigger>
                <SelectValue placeholder="Select…" />
              </SelectTrigger>
              <SelectContent>
                {(contactsQuery.data?.items ?? []).map((c) => (
                  <SelectItem key={c.id} value={c.id}>
                    {c.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div className="flex flex-col gap-2">
              <Label htmlFor="quote-valid">Valid until</Label>
              <Input id="quote-valid" type="date" value={validUntil} onChange={(e) => setValidUntil(e.target.value)} />
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor="quote-qty">Qty</Label>
              <Input id="quote-qty" type="number" step="0.0001" value={qty} onChange={(e) => setQty(e.target.value)} />
            </div>
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor="quote-desc">Line description</Label>
            <Input id="quote-desc" value={description} onChange={(e) => setDescription(e.target.value)} />
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor="quote-price">Unit price</Label>
            <Input id="quote-price" type="number" step="0.01" value={price} onChange={(e) => setPrice(e.target.value)} />
          </div>
          {error && <p className="text-xs text-destructive">{error}</p>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button disabled={!partyId || !description || saving} onClick={() => void create()}>
            {saving ? "Creating…" : "Create & send"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
