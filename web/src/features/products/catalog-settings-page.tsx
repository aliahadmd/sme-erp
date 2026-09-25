import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { toast } from "sonner"

import { catalogApi, type Category, type Tax, type Uom } from "@/features/products/api"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
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

export function CatalogSettingsPage() {
  return (
    <div className="flex flex-col gap-8">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Catalog settings</h1>
        <p className="text-sm text-muted-foreground">Taxes, units of measure, and categories</p>
      </div>
      <TaxesSection />
      <UomsSection />
      <CategoriesSection />
    </div>
  )
}

function TaxesSection() {
  const queryClient = useQueryClient()
  const { data: taxes } = useQuery({ queryKey: ["catalog", "taxes"], queryFn: () => catalogApi.taxes() })
  const [code, setCode] = useState("")
  const [name, setName] = useState("")
  const [rate, setRate] = useState("")

  const create = useMutation({
    mutationFn: () => catalogApi.createTax({ code, name, rate_pct: rate, applies_to: "both" }),
    onSuccess: () => {
      toast.success("Tax created")
      setCode("")
      setName("")
      setRate("")
      void queryClient.invalidateQueries({ queryKey: ["catalog", "taxes"] })
    },
    onError: (err) => toast.error(errorMessage(err)),
  })

  return (
    <section className="flex flex-col gap-3">
      <h2 className="text-lg font-semibold">Taxes</h2>
      <div className="rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Code</TableHead>
              <TableHead>Name</TableHead>
              <TableHead className="text-right">Rate</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {(taxes ?? []).map((tax: Tax) => (
              <TableRow key={tax.id}>
                <TableCell className="font-mono text-xs">{tax.code}</TableCell>
                <TableCell>{tax.name}</TableCell>
                <TableCell className="text-right font-mono text-sm">{Number(tax.rate_pct)}%</TableCell>
              </TableRow>
            ))}
            {(taxes ?? []).length === 0 && (
              <TableRow>
                <TableCell colSpan={3} className="text-center text-muted-foreground">
                  No taxes defined
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>
      <div className="flex items-end gap-2">
        <div className="flex flex-col gap-1">
          <Label htmlFor="tax-code">Code</Label>
          <Input id="tax-code" className="w-24" value={code} onChange={(e) => setCode(e.target.value.toUpperCase())} />
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor="tax-name">Name</Label>
          <Input id="tax-name" className="w-48" value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor="tax-rate">Rate %</Label>
          <Input id="tax-rate" type="number" step="0.01" className="w-24" value={rate} onChange={(e) => setRate(e.target.value)} />
        </div>
        <Button variant="outline" disabled={!code || !name || rate === "" || create.isPending} onClick={() => create.mutate()}>
          Add tax
        </Button>
      </div>
    </section>
  )
}

function UomsSection() {
  const queryClient = useQueryClient()
  const { data: uoms } = useQuery({ queryKey: ["catalog", "uoms"], queryFn: () => catalogApi.uoms() })
  const [code, setCode] = useState("")
  const [name, setName] = useState("")

  const create = useMutation({
    mutationFn: () => catalogApi.createUom({ code, name }),
    onSuccess: () => {
      toast.success("Unit created")
      setCode("")
      setName("")
      void queryClient.invalidateQueries({ queryKey: ["catalog", "uoms"] })
    },
    onError: (err) => toast.error(errorMessage(err)),
  })

  return (
    <section className="flex flex-col gap-3">
      <h2 className="text-lg font-semibold">Units of measure</h2>
      <div className="flex flex-wrap gap-2">
        {(uoms ?? []).map((uom: Uom) => (
          <span key={uom.id} className="rounded-full border px-3 py-1 text-sm">
            <span className="font-mono text-xs">{uom.code}</span> · {uom.name}
          </span>
        ))}
        {(uoms ?? []).length === 0 && (
          <span className="text-sm text-muted-foreground">No units defined</span>
        )}
      </div>
      <div className="flex items-end gap-2">
        <div className="flex flex-col gap-1">
          <Label htmlFor="uom-code">Code</Label>
          <Input id="uom-code" className="w-24" value={code} onChange={(e) => setCode(e.target.value.toUpperCase())} />
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor="uom-name">Name</Label>
          <Input id="uom-name" className="w-48" value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <Button variant="outline" disabled={!code || !name || create.isPending} onClick={() => create.mutate()}>
          Add unit
        </Button>
      </div>
    </section>
  )
}

function CategoriesSection() {
  const queryClient = useQueryClient()
  const { data } = useQuery({ queryKey: ["catalog", "categories"], queryFn: () => catalogApi.categories() })
  const [name, setName] = useState("")

  const create = useMutation({
    mutationFn: () => catalogApi.createCategory({ name }),
    onSuccess: () => {
      toast.success("Category created")
      setName("")
      void queryClient.invalidateQueries({ queryKey: ["catalog", "categories"] })
    },
    onError: (err) => toast.error(errorMessage(err)),
  })

  const remove = async (category: Category) => {
    try {
      await catalogApi.deleteCategory(category.id)
      toast.success("Category deleted")
      void queryClient.invalidateQueries({ queryKey: ["catalog", "categories"] })
    } catch (err) {
      toast.error(errorMessage(err))
    }
  }

  return (
    <section className="flex flex-col gap-3">
      <h2 className="text-lg font-semibold">Categories</h2>
      <div className="flex flex-wrap gap-2">
        {(data?.items ?? []).map((category) => (
          <span key={category.id} className="flex items-center gap-1 rounded-full border px-3 py-1 text-sm">
            {category.name}
            <button
              type="button"
              aria-label={`Delete ${category.name}`}
              className="text-muted-foreground hover:text-destructive"
              onClick={() => void remove(category)}
            >
              ×
            </button>
          </span>
        ))}
        {(data?.items ?? []).length === 0 && (
          <span className="text-sm text-muted-foreground">No categories</span>
        )}
      </div>
      <div className="flex items-end gap-2">
        <div className="flex flex-col gap-1">
          <Label htmlFor="category-name">Name</Label>
          <Input id="category-name" className="w-48" value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <Button variant="outline" disabled={!name || create.isPending} onClick={() => create.mutate()}>
          Add category
        </Button>
      </div>
    </section>
  )
}
