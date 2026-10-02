import { useMutation, useQuery } from "@tanstack/react-query"
import { useState } from "react"

import { reportsApi } from "@/features/reports/api"
import { useAuth } from "@/lib/auth"
import { ApiError } from "@/lib/api/client"
import { formatMoney } from "@/lib/money"
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
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"

function toCsv(rows: Record<string, string | number>[]): string {
  if (rows.length === 0) return ""
  const headers = Object.keys(rows[0])
  const escape = (value: string | number) => {
    let text = String(value)
    // Neutralize spreadsheet formulas (CSV injection) from user-entered names.
    if (/^[=+\-@]/.test(text) && typeof value === "string") text = `'${text}`
    return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text
  }
  return [
    headers.join(","),
    ...rows.map((row) => headers.map((h) => escape(row[h])).join(",")),
  ].join("\n")
}

function downloadCsv(name: string, rows: Record<string, string | number>[]) {
  const blob = new Blob([toCsv(rows)], { type: "text/csv" })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement("a")
  anchor.href = url
  anchor.download = `${name}.csv`
  anchor.click()
  URL.revokeObjectURL(url)
}

export function ReportsPage() {
  const [report, setReport] = useState("sales")
  const [dateFrom, setDateFrom] = useState("")
  const [dateTo, setDateTo] = useState("")
  const [applied, setApplied] = useState<{ from?: string; to?: string }>({})

  const salesQuery = useQuery({
    queryKey: ["reports", "sales", applied],
    queryFn: () => reportsApi.salesByCustomer(applied.from, applied.to),
    enabled: report === "sales",
  })
  const purchasesQuery = useQuery({
    queryKey: ["reports", "purchases", applied],
    queryFn: () => reportsApi.purchasesBySupplier(applied.from, applied.to),
    enabled: report === "purchases",
  })
  const valuationQuery = useQuery({
    queryKey: ["reports", "valuation"],
    queryFn: () => reportsApi.stockValuation(),
    enabled: report === "valuation",
  })
  const agingArQuery = useQuery({
    queryKey: ["reports", "aging-ar"],
    queryFn: () => reportsApi.aging("ar"),
    enabled: report === "aging",
  })
  const agingApQuery = useQuery({
    queryKey: ["reports", "aging-ap"],
    queryFn: () => reportsApi.aging("ap"),
    enabled: report === "aging",
  })
  const taxQuery = useQuery({
    queryKey: ["reports", "tax", applied],
    queryFn: () => reportsApi.taxSummary(applied.from, applied.to),
    enabled: report === "tax",
  })
  const { hasPermission } = useAuth()
  const currentRows = {
    sales: salesQuery.data,
    purchases: purchasesQuery.data,
    valuation: valuationQuery.data,
    aging: { ar: agingArQuery.data, ap: agingApQuery.data },
    tax: taxQuery.data,
  }[report]
  const summary = useMutation({
    mutationFn: () => reportsApi.summarize(report, currentRows ?? []),
  })

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Reports</h1>
        <p className="text-sm text-muted-foreground">Server-side aggregates over posted documents</p>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <Tabs value={report} onValueChange={setReport}>
          <TabsList>
            <TabsTrigger value="sales">Sales by customer</TabsTrigger>
            <TabsTrigger value="purchases">Purchases by supplier</TabsTrigger>
            <TabsTrigger value="valuation">Stock valuation</TabsTrigger>
            <TabsTrigger value="aging">Aging</TabsTrigger>
            <TabsTrigger value="tax">Tax summary</TabsTrigger>
          </TabsList>
        </Tabs>
      </div>

      {hasPermission("reports.view") && (
        <div className="flex flex-col gap-2 rounded-lg border p-3">
          <div className="flex items-center justify-between gap-2">
            <span className="text-sm text-muted-foreground">
              AI summary of the report on screen (assistive — verify the numbers)
            </span>
            <Button
              size="sm"
              variant="outline"
              disabled={summary.isPending || !currentRows}
              onClick={() => summary.mutate()}
            >
              {summary.isPending ? "Summarizing…" : "Summarize with AI"}
            </Button>
          </div>
          {summary.data && <p className="whitespace-pre-wrap text-sm">{summary.data.summary}</p>}
          {summary.error && (
            <p className="text-xs text-destructive">
              {summary.error instanceof ApiError ? summary.error.message : "AI request failed"}
            </p>
          )}
        </div>
      )}

      {["sales", "purchases", "tax"].includes(report) && (
        <div className="flex flex-wrap items-end gap-2">
          <div className="flex flex-col gap-1">
            <Label htmlFor="report-from">From</Label>
            <Input id="report-from" type="date" className="w-40" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} />
          </div>
          <div className="flex flex-col gap-1">
            <Label htmlFor="report-to">To</Label>
            <Input id="report-to" type="date" className="w-40" value={dateTo} onChange={(e) => setDateTo(e.target.value)} />
          </div>
          <Button variant="outline" onClick={() => setApplied({ from: dateFrom, to: dateTo })}>
            Apply
          </Button>
        </div>
      )}

      {(report === "sales" || report === "purchases") && (
        <PartyReport
          title={report === "sales" ? "Sales by customer" : "Purchases by supplier"}
          rows={(report === "sales" ? salesQuery.data : purchasesQuery.data) ?? []}
          isLoading={salesQuery.isLoading || purchasesQuery.isLoading}
        />
      )}

      {report === "valuation" && (
        <div className="rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Warehouse</TableHead>
                <TableHead className="text-right">Products</TableHead>
                <TableHead className="text-right">Qty on hand</TableHead>
                <TableHead className="text-right">Value</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {(valuationQuery.data ?? []).map((row) => (
                <TableRow key={row.warehouse_code}>
                  <TableCell>{row.warehouse_name} ({row.warehouse_code})</TableCell>
                  <TableCell className="text-right">{row.products}</TableCell>
                  <TableCell className="text-right font-mono text-sm">{Number(row.qty_on_hand).toFixed(2)}</TableCell>
                  <TableCell className="text-right font-mono text-sm">{formatMoney(row.value)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <div className="border-t p-2">
            <Button
              size="sm"
              variant="ghost"
              onClick={() =>
                downloadCsv(
                  "stock-valuation",
                  (valuationQuery.data ?? []).map((r) => ({
                    warehouse: r.warehouse_code,
                    products: r.products,
                    qty_on_hand: r.qty_on_hand,
                    value: r.value,
                  })),
                )
              }
            >
              Export CSV
            </Button>
          </div>
        </div>
      )}

      {report === "aging" && (
        <div className="grid gap-4 md:grid-cols-2">
          {[
            { title: "Receivables (AR)", rows: agingArQuery.data ?? [] },
            { title: "Payables (AP)", rows: agingApQuery.data ?? [] },
          ].map((section) => (
            <div key={section.title} className="rounded-lg border">
              <div className="border-b px-4 py-2 text-sm font-semibold">{section.title}</div>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Bucket</TableHead>
                    <TableHead className="text-right">Open amount</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {section.rows.map((row) => (
                    <TableRow key={row.bucket}>
                      <TableCell>{row.bucket}</TableCell>
                      <TableCell className="text-right font-mono text-sm">
                        {formatMoney(row.amount)}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          ))}
        </div>
      )}

      {report === "tax" && (
        <div className="rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Direction</TableHead>
                <TableHead>Tax</TableHead>
                <TableHead className="text-right">Rate</TableHead>
                <TableHead className="text-right">Net</TableHead>
                <TableHead className="text-right">Tax</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {(taxQuery.data ?? []).map((row, i) => (
                <TableRow key={i}>
                  <TableCell className="text-sm">
                    {row.direction === "output" ? "Output (sales)" : "Input (purchases)"}
                  </TableCell>
                  <TableCell>{row.tax_name}</TableCell>
                  <TableCell className="text-right">{row.rate_pct ? `${Number(row.rate_pct)}%` : "—"}</TableCell>
                  <TableCell className="text-right font-mono text-sm">{formatMoney(row.net)}</TableCell>
                  <TableCell className="text-right font-mono text-sm">{formatMoney(row.tax)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <div className="border-t p-2">
            <Button
              size="sm"
              variant="ghost"
              onClick={() =>
                downloadCsv(
                  "tax-summary",
                  (taxQuery.data ?? []).map((r) => ({
                    direction: r.direction,
                    tax_name: r.tax_name ?? "",
                    rate_pct: r.rate_pct ?? "",
                    net: r.net,
                    tax_amount: r.tax,
                  })),
                )
              }
            >
              Export CSV
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}

function PartyReport({
  title,
  rows,
  isLoading,
}: {
  title: string
  rows: { party_name: string; invoice_count: number; total: string }[]
  isLoading: boolean
}) {
  return (
    <div className="rounded-lg border">
      <div className="flex items-center justify-between border-b px-4 py-2">
        <span className="text-sm font-semibold">{title}</span>
        <Button
          size="sm"
          variant="ghost"
          onClick={() =>
            downloadCsv(
              title.toLowerCase().replace(/\s+/g, "-"),
              rows.map((r) => ({ party: r.party_name, invoices: r.invoice_count, total: r.total })),
            )
          }
        >
          Export CSV
        </Button>
      </div>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Party</TableHead>
            <TableHead className="text-right">Invoices</TableHead>
            <TableHead className="text-right">Total</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {isLoading && (
            <TableRow>
              <TableCell colSpan={3} className="text-center text-muted-foreground">
                Loading…
              </TableCell>
            </TableRow>
          )}
          {!isLoading && rows.length === 0 && (
            <TableRow>
              <TableCell colSpan={3} className="text-center text-muted-foreground">
                No posted documents in this period
              </TableCell>
            </TableRow>
          )}
          {rows.map((row, i) => (
            <TableRow key={i}>
              <TableCell>{row.party_name}</TableCell>
              <TableCell className="text-right">{row.invoice_count}</TableCell>
              <TableCell className="text-right font-mono text-sm">{formatMoney(row.total)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}
