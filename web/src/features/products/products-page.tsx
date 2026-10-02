import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Archive, Plus, Search } from "lucide-react"
import { useState } from "react"
import { toast } from "sonner"

import {
  catalogApi,
  type Category,
  type Product,
  type ProductInput,
  type Tax,
  type Uom,
} from "@/features/products/api"
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { ApiError } from "@/lib/api/client"
import { queryKeys } from "@/lib/query-keys"
import { formatMoney } from "@/lib/money"

function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : "Something went wrong"
}

export function ProductsPage() {
  const queryClient = useQueryClient()
  const [search, setSearch] = useState("")
  const [appliedSearch, setAppliedSearch] = useState("")
  const [type, setType] = useState<string>("all")
  const [editing, setEditing] = useState<Product | null>(null)
  const [creating, setCreating] = useState(false)

  const { data, isLoading, refetch } = useQuery({
    queryKey: queryKeys.products({ q: appliedSearch, type }),
    queryFn: () =>
      catalogApi.products({ q: appliedSearch, type: type === "all" ? undefined : type, limit: 50 }),
  })

  const refresh = () => void queryClient.invalidateQueries({ queryKey: queryKeys.products({}) })

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Products</h1>
          <p className="text-sm text-muted-foreground">
            Goods and services you buy and sell
          </p>
        </div>
        <Button onClick={() => setCreating(true)}>
          <Plus className="size-4" /> New product
        </Button>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <Tabs value={type} onValueChange={setType}>
          <TabsList>
            <TabsTrigger value="all">All</TabsTrigger>
            <TabsTrigger value="goods">Goods</TabsTrigger>
            <TabsTrigger value="service">Services</TabsTrigger>
          </TabsList>
        </Tabs>
        <form
          className="flex items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault()
            setAppliedSearch(search)
            refresh()
          }}
        >
          <div className="relative">
            <Search className="absolute top-2.5 left-2 size-4 text-muted-foreground" />
            <Input
              placeholder="Search name or SKU…"
              className="w-64 pl-8"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <Button variant="outline" type="submit">
            Search
          </Button>
        </form>
      </div>

      <div className="rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>SKU</TableHead>
              <TableHead>Name</TableHead>
              <TableHead>Type</TableHead>
              <TableHead className="text-right">Sale price</TableHead>
              <TableHead className="text-right">Cost</TableHead>
              <TableHead className="w-16" />
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
                  No products yet
                </TableCell>
              </TableRow>
            )}
            {data?.items.map((product) => (
              <TableRow key={product.id}>
                <TableCell className="font-mono text-xs">{product.sku}</TableCell>
                <TableCell className="font-medium">{product.name}</TableCell>
                <TableCell>
                  <Badge variant={product.type === "goods" ? "default" : "secondary"}>
                    {product.type}
                  </Badge>
                </TableCell>
                <TableCell className="text-right font-mono text-sm">
                  {formatMoney(product.sale_price)}
                </TableCell>
                <TableCell className="text-right font-mono text-sm">
                  {formatMoney(product.cost_price)}
                </TableCell>
                <TableCell>
                  <div className="flex gap-1">
                    <Button variant="ghost" size="sm" onClick={() => setEditing(product)}>
                      Edit
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label="Archive product"
                      onClick={async () => {
                        try {
                          await catalogApi.archiveProduct(product.id, true)
                          toast.success("Product archived")
                          void refetch()
                        } catch (err) {
                          toast.error(errorMessage(err))
                        }
                      }}
                    >
                      <Archive className="size-4" />
                    </Button>
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      <ProductDialog
        key={editing?.id ?? "new"}
        open={creating || !!editing}
        product={editing}
        onOpenChange={(open) => {
          if (!open) {
            setCreating(false)
            setEditing(null)
          }
        }}
        onDone={() => {
          refresh()
          void refetch()
          setCreating(false)
          setEditing(null)
        }}
      />
    </div>
  )
}

function ProductDialog({
  open,
  product,
  onOpenChange,
  onDone,
}: {
  open: boolean
  product: Product | null
  onOpenChange: (open: boolean) => void
  onDone: () => void
}) {
  const [form, setForm] = useState<ProductInput>({
    name: product?.name ?? "",
    sku: product?.sku ?? "",
    type: product?.type ?? "goods",
    sale_price: product?.sale_price ?? "0",
    cost_price: product?.cost_price ?? "0",
    min_stock: product?.min_stock ?? "0",
    track_inventory: product?.track_inventory ?? true,
    is_purchasable: product?.is_purchasable ?? true,
    is_sellable: product?.is_sellable ?? true,
    category_id: product?.category_id ?? null,
    uom_id: product?.uom_id ?? null,
    sale_tax_id: product?.sale_tax_id ?? null,
    description: product?.description ?? "",
  })
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [generating, setGenerating] = useState(false)

  const uomsQuery = useQuery({ queryKey: ["catalog", "uoms"], queryFn: () => catalogApi.uoms() })
  const taxesQuery = useQuery({ queryFn: () => catalogApi.taxes(), queryKey: ["catalog", "taxes"] })
  const categoriesQuery = useQuery({
    queryFn: () => catalogApi.categories(),
    queryKey: ["catalog", "categories"],
  })

  const uoms: Uom[] = uomsQuery.data ?? []
  const taxes: Tax[] = taxesQuery.data ?? []
  const categories: Category[] = categoriesQuery.data?.items ?? []

  const set = (patch: Partial<ProductInput>) => setForm((p) => ({ ...p, ...patch }))

  const save = async () => {
    setSaving(true)
    setError(null)
    const payload: ProductInput = {
      ...form,
      track_inventory: form.type === "service" ? false : form.track_inventory,
    }
    try {
      if (product) {
        await catalogApi.updateProduct(product.id, payload)
        toast.success("Product updated")
      } else {
        await catalogApi.createProduct(payload)
        toast.success("Product created")
      }
      onDone()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>{product ? `Edit ${product.name}` : "New product"}</DialogTitle>
        </DialogHeader>
        <Tabs defaultValue="general">
          <TabsList>
            <TabsTrigger value="general">General</TabsTrigger>
            <TabsTrigger value="pricing">Pricing & Tax</TabsTrigger>
          </TabsList>
          <TabsContent value="general" className="flex flex-col gap-4 pt-2">
            <div className="flex flex-col gap-2">
              <Label htmlFor="product-name">Name *</Label>
              <Input
                id="product-name"
                value={form.name}
                onChange={(e) => set({ name: e.target.value })}
              />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="flex flex-col gap-2">
                <Label htmlFor="product-sku">SKU (auto if blank)</Label>
                <Input id="product-sku" value={form.sku ?? ""} onChange={(e) => set({ sku: e.target.value })} />
              </div>
              <div className="flex flex-col gap-2">
                <Label>Type</Label>
                <Select
                  value={form.type}
                  onValueChange={(v) => set({ type: v as "goods" | "service" })}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="goods">Goods</SelectItem>
                    <SelectItem value="service">Service</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="flex flex-col gap-2">
                <Label>Category</Label>
                <Select
                  value={form.category_id ?? "none"}
                  onValueChange={(v) => set({ category_id: v === "none" ? null : v })}
                >
                  <SelectTrigger>
                    <SelectValue placeholder="None" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="none">None</SelectItem>
                    {categories.map((c) => (
                      <SelectItem key={c.id} value={c.id}>
                        {c.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="flex flex-col gap-2">
                <Label>Unit of measure</Label>
                <Select
                  value={form.uom_id ?? "none"}
                  onValueChange={(v) => set({ uom_id: v === "none" ? null : v })}
                >
                  <SelectTrigger>
                    <SelectValue placeholder="None" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="none">None</SelectItem>
                    {uoms.map((u) => (
                      <SelectItem key={u.id} value={u.id}>
                        {u.code}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor="product-description">Description</Label>
              <div className="flex gap-2">
                <Input
                  id="product-description"
                  value={form.description ?? ""}
                  onChange={(e) => set({ description: e.target.value })}
                />
                {product && (
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={generating}
                    onClick={async () => {
                      setGenerating(true)
                      try {
                        const result = await catalogApi.generateDescription(product.id)
                        set({ description: result.description })
                        toast.success(`Generated with ${result.model}`)
                      } catch (err) {
                        toast.error(errorMessage(err))
                      } finally {
                        setGenerating(false)
                      }
                    }}
                  >
                    {generating ? "Generating…" : "✨ AI"}
                  </Button>
                )}
              </div>
            </div>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                className="size-4 accent-primary"
                checked={form.type === "service" ? false : !!form.track_inventory}
                disabled={form.type === "service"}
                onChange={(e) => set({ track_inventory: e.target.checked })}
              />
              Track inventory {form.type === "service" && "(not available for services)"}
            </label>
          </TabsContent>
          <TabsContent value="pricing" className="flex flex-col gap-4 pt-2">
            <div className="grid grid-cols-3 gap-4">
              <div className="flex flex-col gap-2">
                <Label htmlFor="product-sale-price">Sale price</Label>
                <Input
                  id="product-sale-price"
                  type="number"
                  step="0.01"
                  value={form.sale_price}
                  onChange={(e) => set({ sale_price: e.target.value })}
                />
              </div>
              <div className="flex flex-col gap-2">
                <Label htmlFor="product-cost-price">Cost price</Label>
                <Input
                  id="product-cost-price"
                  type="number"
                  step="0.01"
                  value={form.cost_price}
                  onChange={(e) => set({ cost_price: e.target.value })}
                />
              </div>
              <div className="flex flex-col gap-2">
                <Label htmlFor="product-min-stock">Min stock</Label>
                <Input
                  id="product-min-stock"
                  type="number"
                  step="0.0001"
                  disabled={form.type === "service"}
                  value={form.min_stock}
                  onChange={(e) => set({ min_stock: e.target.value })}
                />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="flex flex-col gap-2">
                <Label>Sale tax</Label>
                <Select
                  value={form.sale_tax_id ?? "none"}
                  onValueChange={(v) => set({ sale_tax_id: v === "none" ? null : v })}
                >
                  <SelectTrigger>
                    <SelectValue placeholder="No tax" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="none">No tax</SelectItem>
                    {taxes
                      .filter((t) => t.applies_to !== "purchase")
                      .map((t) => (
                        <SelectItem key={t.id} value={t.id}>
                          {t.name} ({Number(t.rate_pct)}%)
                        </SelectItem>
                      ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="flex flex-col gap-2">
                <Label>Purchase tax</Label>
                <Select
                  value={form.purchase_tax_id ?? "none"}
                  onValueChange={(v) => set({ purchase_tax_id: v === "none" ? null : v })}
                >
                  <SelectTrigger>
                    <SelectValue placeholder="No tax" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="none">No tax</SelectItem>
                    {taxes
                      .filter((t) => t.applies_to !== "sale")
                      .map((t) => (
                        <SelectItem key={t.id} value={t.id}>
                          {t.name} ({Number(t.rate_pct)}%)
                        </SelectItem>
                      ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
          </TabsContent>
        </Tabs>
        {error && <p className="text-xs text-destructive">{error}</p>}
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button disabled={saving || !form.name} onClick={() => void save()}>
            {saving ? "Saving…" : product ? "Save" : "Create"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
