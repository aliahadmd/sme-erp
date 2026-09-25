export function DashboardPage() {
  const kpis = [
    { label: "Sales this month", value: "—", hint: "plan-10" },
    { label: "Open receivables", value: "—", hint: "plan-10" },
    { label: "Open payables", value: "—", hint: "plan-10" },
    { label: "Low stock items", value: "—", hint: "plan-10" },
  ]

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Dashboard</h1>
        <p className="text-sm text-muted-foreground">
          KPIs and charts land with plan-10; master data and documents populate them.
        </p>
      </div>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {kpis.map((kpi) => (
          <div key={kpi.label} className="rounded-lg border p-4">
            <div className="text-sm text-muted-foreground">{kpi.label}</div>
            <div className="mt-2 text-2xl font-semibold">{kpi.value}</div>
            <div className="mt-1 text-xs text-muted-foreground/60">arrives with {kpi.hint}</div>
          </div>
        ))}
      </div>
    </div>
  )
}
