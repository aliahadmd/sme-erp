import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { toast } from "sonner"

import { settingsApi, type OrganizationOut } from "@/features/settings/api"
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
import { ApiError } from "@/lib/api/client"
import { queryKeys } from "@/lib/query-keys"

function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : "Something went wrong"
}

function OrgForm({ org }: { org: OrganizationOut }) {
  const queryClient = useQueryClient()
  const [form, setForm] = useState<Partial<OrganizationOut>>(org)

  const save = useMutation({
    mutationFn: () => settingsApi.updateOrganization(form),
    onSuccess: () => {
      toast.success("Organization updated")
      void queryClient.invalidateQueries({ queryKey: queryKeys.organization() })
    },
    onError: (err) => toast.error(errorMessage(err)),
  })

  const field = (key: keyof OrganizationOut, label: string) => (
    <div className="flex flex-col gap-2">
      <Label htmlFor={`org-${key}`}>{label}</Label>
      <Input
        id={`org-${key}`}
        value={(form[key] as string) ?? ""}
        onChange={(e) => setForm((prev) => ({ ...prev, [key]: e.target.value }))}
      />
    </div>
  )

  return (
    <div className="flex flex-col gap-4">
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {field("name", "Name")}
        {field("legal_name", "Legal name")}
        {field("tax_id", "Tax ID")}
        {field("base_currency", "Base currency (ISO)")}
        {field("email", "Email")}
        {field("phone", "Phone")}
        {field("address_line1", "Address line 1")}
        {field("city", "City")}
        {field("country", "Country code")}
      </div>
      <div>
        <Button disabled={save.isPending} onClick={() => save.mutate()}>
          {save.isPending ? "Saving…" : "Save organization"}
        </Button>
      </div>
    </div>
  )
}

export function OrganizationPage() {
  const queryClient = useQueryClient()
  const orgQuery = useQuery({
    queryKey: queryKeys.organization(),
    queryFn: () => settingsApi.organization(),
  })
  const branchesQuery = useQuery({
    queryKey: queryKeys.branches(),
    queryFn: () => settingsApi.branches(),
  })

  const [branchCode, setBranchCode] = useState("")
  const [branchName, setBranchName] = useState("")

  const addBranch = useMutation({
    mutationFn: () => settingsApi.createBranch({ code: branchCode, name: branchName }),
    onSuccess: () => {
      toast.success("Branch created")
      setBranchCode("")
      setBranchName("")
      void queryClient.invalidateQueries({ queryKey: queryKeys.branches() })
    },
    onError: (err) => toast.error(errorMessage(err)),
  })

  return (
    <div className="flex flex-col gap-8">
      <div className="flex flex-col gap-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Organization</h1>
          <p className="text-sm text-muted-foreground">Company profile used on documents</p>
        </div>
        {orgQuery.data && <OrgForm key={orgQuery.data.id} org={orgQuery.data} />}
      </div>

      <div className="flex flex-col gap-4">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">Branches</h2>
          <p className="text-sm text-muted-foreground">Locations / warehouses of the company</p>
        </div>
        <div className="rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Code</TableHead>
                <TableHead>Name</TableHead>
                <TableHead>Active</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {branchesQuery.data?.map((branch) => (
                <TableRow key={branch.id}>
                  <TableCell className="font-mono text-xs">{branch.code}</TableCell>
                  <TableCell>{branch.name}</TableCell>
                  <TableCell>{branch.is_active ? "yes" : "no"}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
        <div className="flex items-end gap-2">
          <div className="flex flex-col gap-1">
            <Label htmlFor="branch-code">Code</Label>
            <Input
              id="branch-code"
              className="w-28"
              value={branchCode}
              onChange={(e) => setBranchCode(e.target.value.toUpperCase())}
            />
          </div>
          <div className="flex flex-col gap-1">
            <Label htmlFor="branch-name">Name</Label>
            <Input
              id="branch-name"
              value={branchName}
              onChange={(e) => setBranchName(e.target.value)}
            />
          </div>
          <Button
            variant="outline"
            disabled={!branchCode || !branchName || addBranch.isPending}
            onClick={() => addBranch.mutate()}
          >
            Add branch
          </Button>
        </div>
      </div>
    </div>
  )
}
