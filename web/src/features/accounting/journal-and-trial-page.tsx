import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { toast } from "sonner"

import { accountingApi } from "@/features/accounting/api"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
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

function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : "Something went wrong"
}

const SOURCE_LABELS: Record<string, string> = {
  ar_invoice: "AR invoice",
  ap_invoice: "AP bill",
  payment: "Payment",
  delivery: "Delivery (COGS)",
  adjustment: "Stock adjustment",
  manual: "Manual",
}

export function JournalPage() {
  const queryClient = useQueryClient()
  const [manualOpen, setManualOpen] = useState(false)
  const { data, isLoading } = useQuery({
    queryKey: ["accounting", "journal", {}],
    queryFn: () => accountingApi.journalEntries({ limit: 50 }),
  })
  const invalidate = () => void queryClient.invalidateQueries({ queryKey: ["accounting"] })

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Journal entries</h1>
          <p className="text-sm text-muted-foreground">
            Business events post automatically — manual entries must balance
          </p>
        </div>
        <Button onClick={() => setManualOpen(true)}>New manual entry</Button>
      </div>

      <div className="flex flex-col gap-3">
        {isLoading && <p className="text-sm text-muted-foreground">Loading…</p>}
        {(data?.items ?? []).map((entry) => {
          const totalDebit = entry.lines.reduce((s, l) => s + Number(l.debit), 0)
          return (
            <div key={entry.id} className="rounded-lg border">
              <div className="flex flex-wrap items-center gap-2 border-b px-4 py-2">
                <span className="font-mono text-xs font-medium">{entry.number}</span>
                <span className="text-sm text-muted-foreground">{entry.entry_date}</span>
                <Badge variant="outline">{SOURCE_LABELS[entry.source_type] ?? entry.source_type}</Badge>
                <span className="text-sm text-muted-foreground">{entry.memo}</span>
                <span className="ml-auto font-mono text-sm">{totalDebit.toFixed(2)}</span>
              </div>
              <Table>
                <TableBody>
                  {entry.lines.map((line) => (
                    <TableRow key={line.id}>
                      <TableCell className="w-20 pl-4 font-mono text-xs">
                        {line.account_code}
                      </TableCell>
                      <TableCell>{line.account_name}</TableCell>
                      <TableCell className="w-32 text-right font-mono text-sm">
                        {Number(line.debit) ? Number(line.debit).toFixed(2) : ""}
                      </TableCell>
                      <TableCell className="w-32 pr-4 text-right font-mono text-sm">
                        {Number(line.credit) ? Number(line.credit).toFixed(2) : ""}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )
        })}
      </div>

      <ManualEntryDialog open={manualOpen} onOpenChange={setManualOpen} onDone={invalidate} />
    </div>
  )
}

function ManualEntryDialog({
  open,
  onOpenChange,
  onDone,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onDone: () => void
}) {
  const queryClient = useQueryClient()
  const [memo, setMemo] = useState("")
  const [debitAccountId, setDebitAccountId] = useState("")
  const [creditAccountId, setCreditAccountId] = useState("")
  const [amount, setAmount] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  const accountsQuery = useQuery({
    queryKey: ["accounting", "accounts"],
    queryFn: () => accountingApi.accounts(),
    enabled: open,
  })
  const accounts = accountsQuery.data ?? []

  const save = async () => {
    setSaving(true)
    setError(null)
    try {
      await accountingApi.createEntry({
        memo,
        lines: [
          { account_id: debitAccountId, debit: amount, credit: "0" },
          { account_id: creditAccountId, debit: "0", credit: amount },
        ],
      })
      toast.success("Entry posted")
      void queryClient.invalidateQueries({ queryKey: ["accounting"] })
      onDone()
      onOpenChange(false)
      setMemo("")
      setAmount("")
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Manual journal entry</DialogTitle>
        </DialogHeader>
        <div className="flex flex-col gap-4">
          <div className="flex flex-col gap-2">
            <Label htmlFor="je-memo">Memo</Label>
            <Input id="je-memo" value={memo} onChange={(e) => setMemo(e.target.value)} />
          </div>
          <div className="flex flex-col gap-2">
            <Label>Debit account</Label>
            <Select value={debitAccountId} onValueChange={(v) => setDebitAccountId(v ?? "")}>
              <SelectTrigger>
                <SelectValue placeholder="Select…" />
              </SelectTrigger>
              <SelectContent>
                {accounts.map((a) => (
                  <SelectItem key={a.id} value={a.id}>
                    {a.code} · {a.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="flex flex-col gap-2">
            <Label>Credit account</Label>
            <Select value={creditAccountId} onValueChange={(v) => setCreditAccountId(v ?? "")}>
              <SelectTrigger>
                <SelectValue placeholder="Select…" />
              </SelectTrigger>
              <SelectContent>
                {accounts.map((a) => (
                  <SelectItem key={a.id} value={a.id}>
                    {a.code} · {a.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor="je-amount">Amount</Label>
            <Input
              id="je-amount"
              type="number"
              step="0.01"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
            />
          </div>
          {error && <p className="text-xs text-destructive">{error}</p>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            disabled={!memo || !debitAccountId || !creditAccountId || !amount || saving}
            onClick={() => void save()}
          >
            {saving ? "Posting…" : "Post entry"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export function TrialBalancePage() {
  const { data, isLoading } = useQuery({
    queryKey: ["accounting", "trial-balance"],
    queryFn: () => accountingApi.trialBalance(),
  })

  const totalDebit = (data ?? []).reduce((s, r) => s + Number(r.total_debit), 0)
  const totalCredit = (data ?? []).reduce((s, r) => s + Number(r.total_credit), 0)
  const balanced = Math.abs(totalDebit - totalCredit) < 0.001

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Trial balance</h1>
        <p className="text-sm text-muted-foreground">
          Must always sum to zero — the integrity promise of the books
        </p>
      </div>
      <div className="rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Code</TableHead>
              <TableHead>Account</TableHead>
              <TableHead>Type</TableHead>
              <TableHead className="text-right">Debits</TableHead>
              <TableHead className="text-right">Credits</TableHead>
              <TableHead className="text-right">Balance</TableHead>
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
            {(data ?? []).map((row) => (
              <TableRow key={row.account_id}>
                <TableCell className="font-mono text-xs">{row.code}</TableCell>
                <TableCell>{row.name}</TableCell>
                <TableCell>
                  <Badge variant="outline">{row.type}</Badge>
                </TableCell>
                <TableCell className="text-right font-mono text-sm">
                  {Number(row.total_debit) ? Number(row.total_debit).toFixed(2) : ""}
                </TableCell>
                <TableCell className="text-right font-mono text-sm">
                  {Number(row.total_credit) ? Number(row.total_credit).toFixed(2) : ""}
                </TableCell>
                <TableCell className="text-right font-mono text-sm">
                  {Number(row.balance).toFixed(2)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      <div className="flex justify-end">
        <div className="w-64 rounded-lg border p-3 text-sm">
          <div className="flex justify-between">
            <span className="text-muted-foreground">Total debits</span>
            <span className="font-mono">{totalDebit.toFixed(2)}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-muted-foreground">Total credits</span>
            <span className="font-mono">{totalCredit.toFixed(2)}</span>
          </div>
          <div className="mt-1 flex justify-between border-t pt-2 font-semibold">
            <span>{balanced ? "Balanced ✓" : "OUT OF BALANCE"}</span>
            <span className="font-mono">{(totalDebit - totalCredit).toFixed(2)}</span>
          </div>
        </div>
      </div>
    </div>
  )
}
