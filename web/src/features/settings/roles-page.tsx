import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { toast } from "sonner"

import { settingsApi, type PermissionOut } from "@/features/settings/api"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
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
import { queryKeys } from "@/lib/query-keys"

function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : "Something went wrong"
}

export function RolesPage() {
  const queryClient = useQueryClient()
  const [createOpen, setCreateOpen] = useState(false)

  const rolesQuery = useQuery({ queryKey: queryKeys.roles(), queryFn: () => settingsApi.roles() })
  const permissionsQuery = useQuery({
    queryKey: queryKeys.permissions(),
    queryFn: () => settingsApi.permissions(),
  })

  const invalidate = () => void queryClient.invalidateQueries({ queryKey: queryKeys.roles() })

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Roles</h1>
          <p className="text-sm text-muted-foreground">
            System roles are managed in code; create custom roles for special cases
          </p>
        </div>
        <Button onClick={() => setCreateOpen(true)}>New role</Button>
      </div>

      <div className="rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Role</TableHead>
              <TableHead>Code</TableHead>
              <TableHead>Type</TableHead>
              <TableHead>Permissions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rolesQuery.data?.map((role) => (
              <TableRow key={role.id}>
                <TableCell className="font-medium">
                  {role.name}
                  {role.description && (
                    <div className="text-xs font-normal text-muted-foreground">
                      {role.description}
                    </div>
                  )}
                </TableCell>
                <TableCell className="font-mono text-xs">{role.code}</TableCell>
                <TableCell>
                  <Badge variant={role.is_system ? "default" : "secondary"}>
                    {role.is_system ? "system" : "custom"}
                  </Badge>
                </TableCell>
                <TableCell className="max-w-md text-sm text-muted-foreground">
                  {role.permission_codes.length} permissions
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      <CreateRoleDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        permissions={permissionsQuery.data ?? []}
        onDone={invalidate}
      />
    </div>
  )
}

function CreateRoleDialog({
  open,
  onOpenChange,
  permissions,
  onDone,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  permissions: PermissionOut[]
  onDone: () => void
}) {
  const [code, setCode] = useState("")
  const [name, setName] = useState("")
  const [selected, setSelected] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)

  const modules = [...new Set(permissions.map((p) => p.module))]

  const create = useMutation({
    mutationFn: () =>
      settingsApi.createRole({ code, name, permission_codes: selected }),
    onSuccess: () => {
      toast.success("Role created")
      onDone()
      onOpenChange(false)
      setCode("")
      setName("")
      setSelected([])
      setError(null)
    },
    onError: (err) => setError(errorMessage(err)),
  })

  const toggle = (permCode: string) =>
    setSelected((prev) =>
      prev.includes(permCode) ? prev.filter((c) => c !== permCode) : [...prev, permCode],
    )

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>New custom role</DialogTitle>
        </DialogHeader>
        <div className="flex flex-col gap-4">
          <div className="grid grid-cols-2 gap-4">
            <div className="flex flex-col gap-2">
              <Label htmlFor="role-code">Code (lowercase)</Label>
              <Input
                id="role-code"
                placeholder="team_lead"
                value={code}
                onChange={(e) => setCode(e.target.value)}
              />
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor="role-name">Name</Label>
              <Input
                id="role-name"
                placeholder="Team Lead"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </div>
          </div>
          <div className="max-h-72 overflow-y-auto rounded-md border p-3">
            {modules.map((module) => (
              <div key={module} className="mb-3 last:mb-0">
                <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  {module}
                </div>
                <div className="grid grid-cols-1 gap-1 sm:grid-cols-2">
                  {permissions
                    .filter((p) => p.module === module)
                    .map((p) => (
                      <label key={p.code} className="flex items-center gap-2 text-sm">
                        <input
                          type="checkbox"
                          checked={selected.includes(p.code)}
                          onChange={() => toggle(p.code)}
                          className="size-4 accent-primary"
                        />
                        <span className="font-mono text-xs">{p.code}</span>
                      </label>
                    ))}
                </div>
              </div>
            ))}
          </div>
          <div className="text-xs text-muted-foreground">{selected.length} selected</div>
          {error && <p className="text-xs text-destructive">{error}</p>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button disabled={create.isPending || !code || !name} onClick={() => create.mutate()}>
            {create.isPending ? "Creating…" : "Create role"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
