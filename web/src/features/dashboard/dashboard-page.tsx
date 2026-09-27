import { useQuery } from "@tanstack/react-query"
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts"
import { Link } from "react-router"

import { reportsApi } from "@/features/reports/api"
import { useAuth } from "@/lib/auth"
import { queryKeys } from "@/lib/query-keys"

function KpiCard({
  label,
  value,
  to,
}: {
  label: string
  value: string
  to?: string
}) {
  const content = (
    <div className="rounded-lg border p-4 transition-colors hover:bg-muted/40">
      <div className="text-sm text-muted-foreground">{label}</div>
      <div className="mt-2 text-2xl font-semibold">{value}</div>
    </div>
  )
  return to ? <Link to={to}>{content}</Link> : content
}

export function DashboardPage() {
  const { user } = useAuth()
  const { data } = useQuery({
    queryKey: queryKeys.dashboard(),
    queryFn: reportsApi.dashboard,
    refetchInterval: 60_000,
  })

  const chartData = (data?.weeks ?? []).map((w) => ({
    week: w.week_start.slice(5),
    Sales: Number(w.sales),
    Purchases: Number(w.purchases),
  }))
  const hasData = chartData.some((p) => p.Sales > 0 || p.Purchases > 0)

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">
          Welcome back{user ? `, ${user.full_name.split(" ")[0]}` : ""}
        </h1>
        <p className="text-sm text-muted-foreground">
          Your business at a glance — updated live from the books
        </p>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {[
          { label: "Sales this month", value: Number(data?.sales_mtd ?? 0).toFixed(2), to: "/invoicing/ar" },
          { label: "Open receivables", value: Number(data?.open_ar ?? 0).toFixed(2), to: "/invoicing/ar" },
          { label: "Open payables", value: Number(data?.open_ap ?? 0).toFixed(2), to: "/invoicing/ap" },
          { label: "Low stock items", value: String(data?.low_stock_count ?? 0), to: "/inventory?low=1" },
        ].map((kpi, i) => (
          <div
            key={kpi.label}
            className="stagger-item"
            style={{ animationDelay: `${i * 40}ms` }}
          >
            <Link to={kpi.to ?? "#"} className="block rounded-lg border p-4 transition-colors hover:bg-muted/40">
              <div className="text-sm text-muted-foreground">{kpi.label}</div>
              <div className="mt-2 text-2xl font-semibold">{kpi.value}</div>
            </Link>
          </div>
        ))}
      </div>

      <div className="rounded-lg border p-4">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold">Sales vs purchases — last 12 weeks</h2>
          <Link to="/reports" className="text-sm text-muted-foreground underline-offset-4 hover:underline">
            All reports →
          </Link>
        </div>
        {hasData ? (
          <div className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chartData}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="week" fontSize={12} />
                <YAxis fontSize={12} />
                <Tooltip />
                <Legend />
                <Bar dataKey="Sales" fill="var(--color-primary)" radius={[3, 3, 0, 0]} />
                <Bar dataKey="Purchases" fill="var(--color-muted-foreground)" radius={[3, 3, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        ) : (
          <div className="flex h-40 items-center justify-center text-sm text-muted-foreground">
            Post invoices to see the trend — or run <code className="mx-1">make seed-demo</code>
            for sample data
          </div>
        )}
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <KpiCard label="Purchases this month" value={Number(data?.purchases_mtd ?? 0).toFixed(2)} to="/purchasing" />
        <KpiCard label="Reports" value="Sales · Stock · Aging · Tax" to="/reports" />
      </div>
    </div>
  )
}
