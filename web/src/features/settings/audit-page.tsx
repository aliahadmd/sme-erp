import { useQuery } from "@tanstack/react-query"
import { useState } from "react"

import { settingsApi } from "@/features/settings/api"
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
import { queryKeys } from "@/lib/query-keys"

export function AuditPage() {
  const [entityType, setEntityType] = useState("")
  const [action, setAction] = useState("")
  const [applied, setApplied] = useState<{ entity_type?: string; action?: string }>({})

  const { data, isLoading, refetch, isFetching } = useQuery({
    queryKey: queryKeys.auditLogs(applied),
    queryFn: () => settingsApi.auditLogs({ limit: 50, ...applied }),
  })

  const apply = () => {
    setApplied({
      entity_type: entityType || undefined,
      action: action || undefined,
    })
    void refetch()
  }

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Audit log</h1>
        <p className="text-sm text-muted-foreground">
          Every create/update/confirm/post action, with actor and before/after values
        </p>
      </div>

      <div className="flex flex-wrap items-end gap-2">
        <div className="flex flex-col gap-1">
          <label className="text-xs text-muted-foreground" htmlFor="audit-entity">
            Entity type
          </label>
          <Input
            id="audit-entity"
            placeholder="core.user"
            className="w-44"
            value={entityType}
            onChange={(e) => setEntityType(e.target.value)}
          />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs text-muted-foreground" htmlFor="audit-action">
            Action
          </label>
          <Input
            id="audit-action"
            placeholder="login, create, update…"
            className="w-44"
            value={action}
            onChange={(e) => setAction(e.target.value)}
          />
        </div>
        <Button variant="outline" onClick={apply} disabled={isFetching}>
          Filter
        </Button>
      </div>

      <div className="rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>When</TableHead>
              <TableHead>Action</TableHead>
              <TableHead>Entity</TableHead>
              <TableHead>Entity ID</TableHead>
              <TableHead>Actor</TableHead>
              <TableHead>IP</TableHead>
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
                  No matching entries
                </TableCell>
              </TableRow>
            )}
            {data?.items.map((entry) => (
              <TableRow key={entry.id}>
                <TableCell className="whitespace-nowrap text-sm">
                  {new Date(entry.created_at).toLocaleString()}
                </TableCell>
                <TableCell>
                  <Badge
                    variant={
                      entry.action === "login"
                        ? "secondary"
                        : entry.action === "create"
                          ? "default"
                          : "outline"
                    }
                  >
                    {entry.action}
                  </Badge>
                </TableCell>
                <TableCell className="font-mono text-xs">{entry.entity_type}</TableCell>
                <TableCell className="max-w-40 truncate font-mono text-xs">
                  {entry.entity_id ?? "—"}
                </TableCell>
                <TableCell className="max-w-40 truncate font-mono text-xs">
                  {entry.user_id ?? "—"}
                </TableCell>
                <TableCell className="text-xs text-muted-foreground">
                  {entry.ip ?? "—"}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      <div className="text-xs text-muted-foreground">
        Showing {data?.items.length ?? 0} of {data?.total ?? 0} entries
      </div>
    </div>
  )
}
