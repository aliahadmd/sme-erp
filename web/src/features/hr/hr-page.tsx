import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { toast } from "sonner"

import { hrApi, type Employee } from "@/features/hr/api"
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
import { useAuth } from "@/lib/auth"

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
      hrApi.createEmployee({ full_name: fullName, position }),
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
  const [typeName, setTypeName] = useState("")
  const [typeDays, setTypeDays] = useState("20")

  const { hasPermission } = useAuth()
  const canSeeEmployees = hasPermission("hr.employee.read")
  const canDecide = hasPermission("hr.leave.approve")
  const canManageTypes = hasPermission("hr.employee.update")

  const employees = useQuery({
    queryKey: ["hr", "employees"],
    queryFn: () => hrApi.employees(),
    enabled: canSeeEmployees,
  })
  // Self-service: the user's own record (absent for HR staff without one).
  const me = useQuery({
    queryKey: ["hr", "employees", "me"],
    queryFn: () => hrApi.myEmployee(),
    retry: false,
  })
  const selectable: Employee[] = canSeeEmployees ? (employees.data ?? []) : me.data ? [me.data] : []
  const selectedEmployee = employeeId || (!canSeeEmployees && me.data ? me.data.id : "")
  const leaveTypes = useQuery({
    queryKey: ["hr", "leave-types"],
    queryFn: () => hrApi.leaveTypes(),
  })
  const requests = useQuery({
    queryKey: ["hr", "leave-requests", {}],
    queryFn: () => hrApi.leaveRequests({}),
  })
  const invalidate = () => void queryClient.invalidateQueries({ queryKey: ["hr", "leave-requests"] })

  const employeeName = (id: string) =>
    selectable.find((e) => e.id === id)?.full_name ?? id.slice(0, 8)
  const leaveTypeName = (id: string) =>
    (leaveTypes.data ?? []).find((t) => t.id === id)?.name ?? id.slice(0, 8)

  const addType = useMutation({
    mutationFn: () => hrApi.createLeaveType({ name: typeName, days_per_year: typeDays }),
    onSuccess: (created) => {
      toast.success(`Leave type "${created.name}" added`)
      setTypeName("")
      void queryClient.invalidateQueries({ queryKey: ["hr", "leave-types"] })
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "Failed"),
  })

  const decide = useMutation({
    mutationFn: (vars: { id: string; action: "approve" | "reject" | "cancel" }) =>
      vars.action === "approve"
        ? hrApi.approveLeave(vars.id)
        : vars.action === "reject"
          ? hrApi.rejectLeave(vars.id)
          : hrApi.cancelLeave(vars.id),
    onSuccess: (request) => {
      toast.success(`Leave ${request.status}`)
      invalidate()
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "Failed"),
  })

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Leave requests</h1>
        <p className="text-sm text-muted-foreground">
          {canDecide ? "Submit and decide on time off" : "Request time off — working days only"}
        </p>
      </div>

      <div className="flex flex-wrap items-end gap-2 rounded-lg border p-3">
        <div className="flex flex-col gap-1">
          <Label>Employee</Label>
          <select
            className="rounded-md border bg-transparent px-2 py-1.5 text-sm"
            value={selectedEmployee}
            disabled={!canSeeEmployees}
            onChange={(e) => setEmployeeId(e.target.value)}
          >
            <option value="">—</option>
            {selectable.map((emp) => (
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
          disabled={!selectedEmployee || !typeId || !dateFrom || !dateTo}
          onClick={async () => {
            try {
              await hrApi.createLeaveRequest({
                employee_id: selectedEmployee,
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

      {canManageTypes && (
        <div className="flex flex-wrap items-end gap-2 rounded-lg border p-3">
          <div className="flex flex-col gap-1">
            <Label htmlFor="leave-type-name">New leave type</Label>
            <Input
              id="leave-type-name"
              placeholder="Annual leave"
              value={typeName}
              onChange={(e) => setTypeName(e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1">
            <Label htmlFor="leave-type-days">Working days / year</Label>
            <Input
              id="leave-type-days"
              type="number"
              min="0"
              className="w-32"
              value={typeDays}
              onChange={(e) => setTypeDays(e.target.value)}
            />
          </div>
          <Button
            variant="outline"
            disabled={!typeName || !typeDays || addType.isPending}
            onClick={() => addType.mutate()}
          >
            Add type
          </Button>
          {(leaveTypes.data ?? []).length > 0 && (
            <p className="text-xs text-muted-foreground">
              Existing:{" "}
              {(leaveTypes.data ?? []).map((t) => `${t.name} (${Number(t.days_per_year)})`).join(", ")}
            </p>
          )}
        </div>
      )}

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
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {(requests.data ?? []).map((r) => (
              <TableRow key={r.id}>
                <TableCell>{employeeName(r.employee_id)}</TableCell>
                <TableCell>{leaveTypeName(r.type_id)}</TableCell>
                <TableCell className="text-sm">{r.date_from}</TableCell>
                <TableCell className="text-sm">{r.date_to}</TableCell>
                <TableCell className="font-mono text-sm">{Number(r.days)}</TableCell>
                <TableCell>
                  <Badge variant={statusVariant(r.status)}>{r.status}</Badge>
                </TableCell>
                <TableCell>
                  {r.status === "pending" && (
                    <div className="flex flex-wrap justify-end gap-1">
                      {canDecide && r.employee_id !== me.data?.id && (
                        <>
                          <Button
                            size="sm"
                            variant="outline"
                            disabled={decide.isPending}
                            onClick={() => decide.mutate({ id: r.id, action: "approve" })}
                          >
                            Approve
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            disabled={decide.isPending}
                            onClick={() => decide.mutate({ id: r.id, action: "reject" })}
                          >
                            Reject
                          </Button>
                        </>
                      )}
                      {(canDecide || r.employee_id === me.data?.id) && (
                        <Button
                          size="sm"
                          variant="ghost"
                          disabled={decide.isPending}
                          onClick={() => decide.mutate({ id: r.id, action: "cancel" })}
                        >
                          Cancel
                        </Button>
                      )}
                    </div>
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
