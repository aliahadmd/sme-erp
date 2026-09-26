import { useQuery } from "@tanstack/react-query"
import { Plus, Search } from "lucide-react"
import { useState } from "react"
import { Link, useSearchParams } from "react-router"

import { ordersApi, partyLabel, type Order, type OrderModule } from "@/features/documents/api"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { queryKeys } from "@/lib/query-keys"

const STATUS_TABS = ["all", "in progress", "draft", "confirmed", "delivered", "invoiced", "received", "closed", "cancelled"]

function statusesFor(tab: string): string | undefined {
  if (tab === "all") return undefined
  if (tab === "in progress") return undefined // filtered client-side below
  return tab
}

const STATUS_COLORS: Record<string, "default" | "secondary" | "destructive" | "outline"> = {
  draft: "outline",
  confirmed: "default",
  delivered: "default",
  received: "default",
  invoiced: "secondary",
  closed: "secondary",
  cancelled: "destructive",
}

export function OrdersListPage({ module }: { module: OrderModule }) {
  const [searchParams, setSearchParams] = useSearchParams()
  const status = searchParams.get("status") ?? "all"
  const [search, setSearch] = useState("")
  const [appliedSearch, setAppliedSearch] = useState("")

  const { data, isLoading } = useQuery({
    queryKey: queryKeys.salesOrders({ status, q: appliedSearch, module }),
    queryFn: () =>
      ordersApi.list(module, {
        status: statusesFor(status),
        q: appliedSearch,
        limit: 50,
      }),
  })
  const visible = (data?.items ?? []).filter((o) =>
    status === "in progress" ? !["closed", "cancelled"].includes(o.status) : true,
  )

  const label = module === "sales" ? "Sales orders" : "Purchase orders"

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">{label}</h1>
          <p className="text-sm text-muted-foreground">
            Documents are immutable once confirmed — corrections happen by cancelling or reversing
          </p>
        </div>
        <Link to={`/${module}/new`}>
          <Button>
            <Plus className="size-4" /> New {module === "sales" ? "sales order" : "purchase order"}
          </Button>
        </Link>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <Tabs value={status} onValueChange={(v) => setSearchParams(v === "all" ? {} : { status: v })}>
          <TabsList>
            {STATUS_TABS.map((tab) => (
              <TabsTrigger key={tab} value={tab}>
                {tab}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
        <form
          className="flex items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault()
            setAppliedSearch(search)
          }}
        >
          <div className="relative">
            <Search className="absolute top-2.5 left-2 size-4 text-muted-foreground" />
            <Input
              placeholder="Number or party…"
              className="w-60 pl-8"
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
              <TableHead>Number</TableHead>
              <TableHead>{partyLabel[module]}</TableHead>
              <TableHead>Date</TableHead>
              <TableHead>Status</TableHead>
              <TableHead className="text-right">Total</TableHead>
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
            {!isLoading && data?.items.length === 0 && (
              <TableRow>
                <TableCell colSpan={5} className="text-center text-muted-foreground">
                  Nothing here yet
                </TableCell>
              </TableRow>
            )}
            {visible.map((order: Order) => (
              <TableRow key={order.id}>
                <TableCell>
                  <Link
                    to={`/${module}/${order.id}`}
                    className="font-mono text-xs font-medium underline-offset-4 hover:underline"
                  >
                    {order.number}
                  </Link>
                </TableCell>
                <TableCell>
                  {order.customer_name ?? order.supplier_name ?? "—"}
                </TableCell>
                <TableCell className="text-sm">{order.order_date}</TableCell>
                <TableCell>
                  <Badge variant={STATUS_COLORS[order.status] ?? "outline"}>{order.status}</Badge>
                </TableCell>
                <TableCell className="text-right font-mono text-sm">
                  {Number(order.total).toFixed(2)} {order.currency}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  )
}
