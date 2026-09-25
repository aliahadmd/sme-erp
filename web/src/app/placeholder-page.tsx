export function PlaceholderPage({ title, plan }: { title: string; plan: number }) {
  return (
    <div className="flex min-h-[50vh] flex-col items-center justify-center gap-2 rounded-lg border border-dashed p-8 text-center">
      <h1 className="text-xl font-semibold">{title}</h1>
      <p className="max-w-md text-sm text-muted-foreground">
        This section ships with phase-1 plan-{plan}. The layout, navigation, and auth
        shell you are looking at are already live.
      </p>
    </div>
  )
}
