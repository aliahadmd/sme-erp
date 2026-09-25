import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { MoreHorizontal, Plus } from "lucide-react"
import { useState } from "react"
import { toast } from "sonner"

import { settingsApi, type UserOut } from "@/features/settings/api"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { ApiError } from "@/lib/api/client"
import { useAuth } from "@/lib/auth"
import { queryKeys } from "@/lib/query-keys"

function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : "Something went wrong"
}

export function UsersPage() {
  const { hasPermission } = useAuth()
  const queryClient = useQueryClient()
  const [createOpen, setCreateOpen] = useState(false)
  const [editing, setEditing] = useState<UserOut | null>(null)

  const { data, isLoading } = useQuery({
    queryKey: queryKeys.users({}),
    queryFn: () => settingsApi.users({ limit: 100 }),
  })

  const rolesQuery = useQuery({
    queryKey: queryKeys.roles(),
    queryFn: () => settingsApi.roles(),
  })

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: queryKeys.users({}) })
  }

  const toggleActive = useMutation({
    mutationFn: (user: UserOut) =>
      settingsApi.updateUser(user.id, { is_active: !user.is_active }),
    onSuccess: () => {
      toast.success("User updated")
      invalidate()
    },
    onError: (error) => toast.error(errorMessage(error)),
  })

  const canCreate = hasPermission("core.user.create")
  const canUpdate = hasPermission("core.user.update")

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Users</h1>
          <p className="text-sm text-muted-foreground">
            {data?.total ?? "…"} accounts · roles control what each user can do
          </p>
        </div>
        {canCreate && (
          <Button onClick={() => setCreateOpen(true)}>
            <Plus className="size-4" /> New user
          </Button>
        )}
      </div>

      <div className="rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Email</TableHead>
              <TableHead>Name</TableHead>
              <TableHead>Roles</TableHead>
              <TableHead>Active</TableHead>
              <TableHead>Last login</TableHead>
              <TableHead className="w-12" />
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
            {data?.items.map((user) => (
              <TableRow key={user.id}>
                <TableCell className="font-medium">{user.email}</TableCell>
                <TableCell>{user.full_name}</TableCell>
                <TableCell>
                  <div className="flex flex-wrap gap-1">
                    {user.is_superuser && (
                      <span className="rounded bg-primary/10 px-1.5 py-0.5 text-xs font-medium text-primary">
                        superuser
                      </span>
                    )}
                    {user.role_codes.map((code) => (
                      <span
                        key={code}
                        className="rounded bg-muted px-1.5 py-0.5 text-xs text-muted-foreground"
                      >
                        {code}
                      </span>
                    ))}
                  </div>
                </TableCell>
                <TableCell>
                  <Switch
                    checked={user.is_active}
                    disabled={!canUpdate || user.is_superuser}
                    onCheckedChange={() => toggleActive.mutate(user)}
                    aria-label={`Toggle ${user.email}`}
                  />
                </TableCell>
                <TableCell className="text-sm text-muted-foreground">
                  {user.last_login_at
                    ? new Date(user.last_login_at).toLocaleString()
                    : "never"}
                </TableCell>
                <TableCell>
                  {canUpdate && (
                    <DropdownMenu>
                      <DropdownMenuTrigger
                        render={<Button variant="ghost" size="icon" aria-label="User actions" />}
                      >
                        <MoreHorizontal className="size-4" />
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        <DropdownMenuItem onClick={() => setEditing(user)}>
                          Edit
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      <CreateUserDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        roles={rolesQuery.data ?? []}
        onDone={invalidate}
      />
      <EditUserDialog
        key={editing?.id ?? "none"}
        user={editing}
        onOpenChange={(open) => !open && setEditing(null)}
        roles={rolesQuery.data ?? []}
        onDone={invalidate}
      />
    </div>
  )
}

function RoleCheckboxes({
  roles,
  selected,
  onToggle,
}: {
  roles: { code: string; name: string }[]
  selected: string[]
  onToggle: (code: string) => void
}) {
  return (
    <div className="grid grid-cols-2 gap-2">
      {roles.map((role) => (
        <label key={role.code} className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={selected.includes(role.code)}
            onChange={() => onToggle(role.code)}
            className="size-4 accent-primary"
          />
          {role.name}
          <span className="text-xs text-muted-foreground">({role.code})</span>
        </label>
      ))}
    </div>
  )
}

function CreateUserDialog({
  open,
  onOpenChange,
  roles,
  onDone,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  roles: { code: string; name: string }[]
  onDone: () => void
}) {
  const [email, setEmail] = useState("")
  const [fullName, setFullName] = useState("")
  const [password, setPassword] = useState("")
  const [selected, setSelected] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)

  const create = useMutation({
    mutationFn: () =>
      settingsApi.createUser({
        email,
        password,
        full_name: fullName,
        role_codes: selected,
      }),
    onSuccess: () => {
      toast.success("User created")
      onDone()
      onOpenChange(false)
      setEmail("")
      setFullName("")
      setPassword("")
      setSelected([])
      setError(null)
    },
    onError: (err) => setError(errorMessage(err)),
  })

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>New user</DialogTitle>
        </DialogHeader>
        <div className="flex flex-col gap-4">
          <div className="flex flex-col gap-2">
            <Label htmlFor="new-user-email">Email</Label>
            <Input id="new-user-email" value={email} onChange={(e) => setEmail(e.target.value)} />
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor="new-user-name">Full name</Label>
            <Input id="new-user-name" value={fullName} onChange={(e) => setFullName(e.target.value)} />
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor="new-user-password">Password (min 8 chars)</Label>
            <Input
              id="new-user-password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-2">
            <Label>Roles</Label>
            <RoleCheckboxes
              roles={roles}
              selected={selected}
              onToggle={(code) =>
                setSelected((prev) =>
                  prev.includes(code) ? prev.filter((c) => c !== code) : [...prev, code],
                )
              }
            />
          </div>
          {error && <p className="text-xs text-destructive">{error}</p>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button disabled={create.isPending} onClick={() => create.mutate()}>
            {create.isPending ? "Creating…" : "Create user"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function EditUserDialog({
  user,
  onOpenChange,
  roles,
  onDone,
}: {
  user: UserOut | null
  onOpenChange: (open: boolean) => void
  roles: { code: string; name: string }[]
  onDone: () => void
}) {
  const [fullName, setFullName] = useState(user?.full_name ?? "")
  const [password, setPassword] = useState("")
  const [selected, setSelected] = useState<string[]>(user?.role_codes ?? [])
  const [error, setError] = useState<string | null>(null)

  const update = useMutation({
    mutationFn: () => {
      if (!user) throw new Error("No user")
      return settingsApi.updateUser(user.id, {
        full_name: fullName,
        role_codes: selected,
        ...(password ? { password } : {}),
      })
    },
    onSuccess: () => {
      toast.success("User updated")
      onDone()
      onOpenChange(false)
    },
    onError: (err) => setError(errorMessage(err)),
  })

  return (
    <Dialog open={!!user} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Edit {user?.email}</DialogTitle>
        </DialogHeader>
        <div className="flex flex-col gap-4">
          <div className="flex flex-col gap-2">
            <Label htmlFor="edit-user-name">Full name</Label>
            <Input id="edit-user-name" value={fullName} onChange={(e) => setFullName(e.target.value)} />
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor="edit-user-password">New password (optional)</Label>
            <Input
              id="edit-user-password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-2">
            <Label>Roles</Label>
            <RoleCheckboxes
              roles={roles}
              selected={selected}
              onToggle={(code) =>
                setSelected((prev) =>
                  prev.includes(code) ? prev.filter((c) => c !== code) : [...prev, code],
                )
              }
            />
          </div>
          {error && <p className="text-xs text-destructive">{error}</p>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button disabled={update.isPending} onClick={() => update.mutate()}>
            {update.isPending ? "Saving…" : "Save"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
