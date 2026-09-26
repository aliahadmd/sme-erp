import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { toast } from "sonner"

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
import { api } from "@/lib/api/client"

type Currency = { id: string; code: string; name: string }
type Rate = { id: string; currency: string; rate_date: string; rate: string }

export function CurrenciesPage() {
  const queryClient = useQueryClient()
  const [code, setCode] = useState("")
  const [name, setName] = useState("")
  const [rateCode, setRateCode] = useState("")
  const [rateDate, setRateDate] = useState("")
  const [rate, setRate] = useState("")

  const currencies = useQuery({
    queryKey: ["currencies", "list"],
    queryFn: () => api.get<Currency[]>("/api/currencies"),
  })
  const rates = useQuery({
    queryKey: ["currencies", "rates"],
    queryFn: () => api.get<Rate[]>("/api/currencies/rates"),
  })
  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["currencies"] })
  }

  const addCurrency = useMutation({
    mutationFn: () => api.post<Currency>("/api/currencies", { code, name }),
    onSuccess: () => {
      toast.success("Currency added")
      setCode("")
      setName("")
      invalidate()
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "Failed"),
  })
  const upsertRate = useMutation({
    mutationFn: () =>
      api.put<Rate>("/api/currencies/rates", {
        currency: rateCode,
        rate_date: rateDate,
        rate,
      }),
    onSuccess: () => {
      toast.success("Rate saved")
      invalidate()
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "Failed"),
  })

  return (
    <div className="flex flex-col gap-8">
      <div className="flex flex-col gap-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Currencies & FX rates</h1>
          <p className="text-sm text-muted-foreground">
            Documents in foreign currencies post journals at the snapshot rate
          </p>
        </div>
        <div className="flex items-end gap-2">
          <div className="flex flex-col gap-1">
            <Label htmlFor="cur-code">Code</Label>
            <Input id="cur-code" className="w-24" value={code} onChange={(e) => setCode(e.target.value.toUpperCase())} />
          </div>
          <div className="flex flex-col gap-1">
            <Label htmlFor="cur-name">Name</Label>
            <Input id="cur-name" className="w-44" value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <Button variant="outline" disabled={!code || !name || addCurrency.isPending} onClick={() => addCurrency.mutate()}>
            Add currency
          </Button>
        </div>
        <div className="rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Code</TableHead>
                <TableHead>Name</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {(currencies.data ?? []).map((c) => (
                <TableRow key={c.id}>
                  <TableCell className="font-mono text-xs">{c.code}</TableCell>
                  <TableCell>{c.name}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      </div>

      <div className="flex flex-col gap-3">
        <h2 className="text-lg font-semibold">FX rates (per 1 base unit)</h2>
        <div className="flex items-end gap-2">
          <div className="flex flex-col gap-1">
            <Label htmlFor="fx-cur">Currency</Label>
            <Input id="fx-cur" className="w-24" value={rateCode} onChange={(e) => setRateCode(e.target.value.toUpperCase())} />
          </div>
          <div className="flex flex-col gap-1">
            <Label htmlFor="fx-date">Date</Label>
            <Input id="fx-date" type="date" value={rateDate} onChange={(e) => setRateDate(e.target.value)} />
          </div>
          <div className="flex flex-col gap-1">
            <Label htmlFor="fx-rate">Rate</Label>
            <Input id="fx-rate" type="number" step="0.00000001" className="w-32" value={rate} onChange={(e) => setRate(e.target.value)} />
          </div>
          <Button variant="outline" disabled={!rateCode || !rateDate || !rate || upsertRate.isPending} onClick={() => upsertRate.mutate()}>
            Save rate
          </Button>
        </div>
        <div className="rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Currency</TableHead>
                <TableHead>Date</TableHead>
                <TableHead className="text-right">Rate</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {(rates.data ?? []).map((r) => (
                <TableRow key={r.id}>
                  <TableCell className="font-mono text-xs">{r.currency}</TableCell>
                  <TableCell>{r.rate_date}</TableCell>
                  <TableCell className="text-right font-mono text-sm">{Number(r.rate)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      </div>
    </div>
  )
}
