import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { toast } from "sonner"

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
import { api } from "@/lib/api/client"

type NumberingRow = { entity: string; prefix: string; next_number: string }

function NumberingRows({
  rows,
}: {
  rows: NumberingRow[]
}) {
  const queryClient = useQueryClient()
  const [drafts, setDrafts] = useState<Record<string, string>>({})

  const save = useMutation({
    mutationFn: (vars: { entity: string; prefix: string }) =>
      api.put<{ entity: string; prefix: string }>(`/api/numbering/${vars.entity}`, {
        value: { prefix: vars.prefix },
      }),
    onSuccess: (_data, vars) => {
      toast.success(`Prefix for ${vars.entity} updated`)
      setDrafts((prev) => {
        const next = { ...prev }
        delete next[vars.entity]
        return next
      })
      void queryClient.invalidateQueries({ queryKey: ["settings", "numbering"] })
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "Failed"),
  })

  return (
    <>
      <div className="rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Entity</TableHead>
              <TableHead>Prefix</TableHead>
              <TableHead>Next number</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row) => {
              const draft = drafts[row.entity]
              return (
                <TableRow key={row.entity}>
                  <TableCell className="font-medium">{row.entity}</TableCell>
                  <TableCell>
                    <Input
                      className="w-28 font-mono text-xs"
                      value={draft ?? row.prefix}
                      onChange={(e) =>
                        setDrafts((prev) => ({ ...prev, [row.entity]: e.target.value }))
                      }
                    />
                  </TableCell>
                  <TableCell className="font-mono text-xs">{row.next_number}</TableCell>
                  <TableCell className="w-24">
                    {draft !== undefined && draft !== row.prefix && (
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={save.isPending}
                        onClick={() => save.mutate({ entity: row.entity, prefix: draft })}
                      >
                        Save
                      </Button>
                    )}
                  </TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      </div>
    </>
  )
}

export function NumberingPage() {
  const { data, isLoading } = useQuery({
    queryKey: ["settings", "numbering"],
    queryFn: () => api.get<NumberingRow[]>("/api/numbering"),
  })

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Document numbering</h1>
        <p className="text-sm text-muted-foreground">
          Prefixes apply to newly created documents; counters never reset
        </p>
      </div>
      {isLoading && <p className="text-sm text-muted-foreground">Loading…</p>}
      {data && <NumberingRows rows={data} />}
    </div>
  )
}
