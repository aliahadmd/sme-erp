import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { toast } from "sonner"

import { hrApi, type Employee, type LeaveRequest } from "@/features/hr/api"
import { Badge } from "@/components/ui/badge"
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
import { api } from "@/lib/api/client"

const statusVariant = (status: string) =>
  status === "approved"
    ? "default"
    : status === "rejected" || status === "cancelled"
      ? "destructive"
      : "secondary"

export function EmployeesPage() {
  const queryClient = useQueryClient()
  const [fullName, setFullName] = useState("")
  const [position, setPosition] = useState("")

  const employees = useQuery({
    queryKey: ["hr", "employees"],
    queryFn: () => hrApi.employees(),
  })

  const create = useMutation({
    mutationFn: () =>
      api.post<Employee>("/api/hr/employees", { full_name: fullName, position }),
    onSuccess: () => {
      toast.success("Employee added")
      setFullName("")
      setPosition("")
      void queryClient.invalidateQueries({ queryKey: ["hr", "employees"] })
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "Failed"),
  })

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Employees</h1>
          <p className="text-sm text-muted-foreground">Company directory</p>
        </div>
      </div>
      <div className="flex items-end gap-2">
        <div className="flex flex-col gap-1">
          <Label htmlFor="emp-name">Full name</Label>
          <Input id="emp-name" className="w-52" value={fullName} onChange={(e) => setFullName(e.target.value)} />
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor="emp-position">Position</Label>
          <Input id="emp-position" className="w-44" value={position} onChange={(e) => setPosition(e.target.value)} />
        </div>
        <Button variant="outline" disabled={!fullName || create.isPending} onClick={() => create.mutate()}>
          Add employee
        </Button>
      </div>
      <div className="rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Number</TableHead>
              <TableHead>Name</TableHead>
              <TableHead>Position</TableHead>
              <TableHead>Status</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {(employees.data ?? []).map((emp) => (
              <TableRow key={emp.id}>
                <TableCell className="font-mono text-xs">{emp.number}</TableCell>
                <TableCell className="font-medium">{emp.full_name}</TableCell>
                <TableCell>{emp.position ?? "—"}</TableCell>
                <TableCell>{emp.status}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  )
}

export function LeaveRequestsPage() {
  const queryClient = useQueryClient()
  const [employeeId, setEmployeeId] = useState("")
  const [typeId, setTypeId] = useState("")
  const [dateFrom, setDateFrom] = useState("")
  const [dateTo, setDateTo] = useState("")

  const employees = useQuery({
    queryKey: ["hr", "employees"],
    queryFn: () => hrApi.employees(),
  })
  const leaveTypes = useQuery({
    queryKey: ["hr", "leave-types"],
    queryFn: () => hrApi.leaveTypes(),
  })
  const requests = useQuery({
    queryKey: ["hr", "leave-requests", {}],
    queryFn: () => hrApi.leaveRequests({}),
  })
  const invalidate = () => void queryClient.invalidateQueries({ queryKey: ["hr", "leave"] })

  const employeeName = (id: string) =>
    (employees.data ?? []).find((e) => e.id === id)?.full_name ?? id.slice(0, 8)
  const typeName = (id: string) =>
    (leaveTypes.data ?? []).find((t) => t.id === id)?.name ?? id.slice(0, 8)

  const decide = useMutation({
    mutationFn: (vars: { id: string }) =>
      api.post<LeaveRequest>(`/api/hr/leave-requests/${vars.id}/approve`, undefined),
    onSuccess: () => {
      toast.success("Leave approved")
      invalidate()
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "Failed"),
  })

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Leave requests</h1>
        <p className="text-sm text-muted-foreground">Submit and approve time off</p>
      </div>

      <div className="flex flex-wrap items-end gap-2 rounded-lg border p-3">
        <div className="flex flex-col gap-1">
          <Label>Employee</Label>
          <select
            className="rounded-md border bg-transparent px-2 py-1.5 text-sm"
            value={employeeId}
            onChange={(e) => setEmployeeId(e.target.value)}
          >
            <option value="">—</option>
            {(employees.data ?? []).map((emp) => (
              <option key={emp.id} value={emp.id}>
                {emp.full_name}
              </option>
            ))}
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <Label>Leave type</Label>
          <select
            className="rounded-md border bg-transparent px-2 py-1.5 text-sm"
            value={typeId}
            onChange={(e) => setTypeId(e.target.value)}
          >
            <option value="">—</option>
            {(leaveTypes.data ?? []).map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <Label>From</Label>
          <Input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} />
        </div>
        <div className="flex flex-col gap-1">
          <Label>To</Label>
          <Input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} />
        </div>
        <Button
          variant="outline"
          disabled={!employeeId || !typeId || !dateFrom || !dateTo}
          onClick={async () => {
            try {
              await hrApi.createLeaveRequest({
                employee_id: employeeId,
                type_id: typeId,
                date_from: dateFrom,
                date_to: dateTo,
              })
              toast.success("Leave request submitted")
              invalidate()
            } catch (err) {
              toast.error(err instanceof Error ? err.message : "Failed")
            }
          }}
        >
          Submit
        </Button>
      </div>

      <div className="rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Employee</TableHead>
              <TableHead>Type</TableHead>
              <TableHead>From</TableHead>
              <TableHead>To</TableHead>
              <TableHead>Days</TableHead>
              <TableHead>Status</TableHead>
              <TableHead className="w-24" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {(requests.data ?? []).map((r) => (
              <TableRow key={r.id}>
                <TableCell>{employeeName(r.employee_id)}</TableCell>
                <TableCell>{typeName(r.type_id)}</TableCell>
                <TableCell className="text-sm">{r.date_from}</TableCell>
                <TableCell className="text-sm">{r.date_to}</TableCell>
                <TableCell className="font-mono text-sm">{Number(r.days)}</TableCell>
                <TableCell>
                  <Badge variant={statusVariant(r.status)}>{r.status}</Badge>
                </TableCell>
                <TableCell>
                  {r.status === "pending" && (
                    <Button size="sm" variant="outline" onClick={() => decide.mutate({ id: r.id })}>
                      Approve
                    </Button>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  )
}
