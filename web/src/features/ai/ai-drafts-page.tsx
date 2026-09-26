import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { api } from "@/lib/api/client"

type Product = { id: string; name: string; sku: string }

export function AiDraftsPage() {
  const queryClient = useQueryClient()
  const productsQuery = useQuery({
    queryKey: ["ai", "products"],
    queryFn: () => api.get<{ items: Product[]; total: number }>("/api/catalog/products?limit=200"),
  })
  const draftsQuery = useQuery({
    queryKey: ["ai", "drafts"],
    queryFn: () =>
      api.get<
        { id: string; entity_id: string; draft_text: string; model: string | null }[]
      >("/api/ai/drafts"),
  })
  const products = productsQuery.data?.items ?? []
  const nameOf = (id: string) => products.find((p) => p.id === id)?.name ?? id.slice(0, 8)

  const invalidate = () => void queryClient.invalidateQueries({ queryKey: ["ai"] })

  const generate = useMutation({
    mutationFn: () => api.post<{ queued: number }>("/api/catalog/products/generate-missing", undefined),
    onSuccess: () => {
      toast.success("Queued products for description generation")
      setTimeout(() => void draftsQuery.refetch(), 4000)
      invalidate()
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "Failed"),
  })

  const act = useMutation({
    mutationFn: (vars: { id: string; action: "accept" | "discard" }) =>
      api.post(`/api/ai/drafts/${vars.id}/${vars.action}`, undefined),
    onSuccess: () => {
      invalidate()
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "Failed"),
  })

  const drafts = draftsQuery.data ?? []

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">AI drafts</h1>
          <p className="text-sm text-muted-foreground">
            Generated descriptions awaiting review — accept applies them to the product
          </p>
        </div>
        <Button variant="outline" onClick={() => generate.mutate()}>
          Generate missing descriptions
        </Button>
      </div>

      <div className="rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Product</TableHead>
              <TableHead>Draft description</TableHead>
              <TableHead className="w-40" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {drafts.map((draft) => (
              <TableRow key={draft.id}>
                <TableCell className="font-medium">{nameOf(draft.entity_id)}</TableCell>
                <TableCell className="max-w-md text-sm">{draft.draft_text}</TableCell>
                <TableCell className="flex gap-1">
                  <Button size="sm" onClick={() => act.mutate({ id: draft.id, action: "accept" })}>
                    Accept
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => act.mutate({ id: draft.id, action: "discard" })}
                  >
                    Discard
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  )
}
