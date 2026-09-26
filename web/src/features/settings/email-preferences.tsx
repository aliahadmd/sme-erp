import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Switch } from "@/components/ui/switch"
import { api } from "@/lib/api/client"

type Prefs = { invoice_sent: boolean; payment_received: boolean; overdue_reminder: boolean }

const LABELS: Record<string, string> = {
  invoice_sent: "Invoice sent to customer",
  payment_received: "Payment received",
  overdue_reminder: "Overdue invoice reminders",
}

const DEFAULTS: Prefs = {
  invoice_sent: true,
  payment_received: true,
  overdue_reminder: true,
}

function PrefsForm({
  initial,
  onSaved,
  onCancel,
}: {
  initial: Prefs
  onSaved: () => void
  onCancel: () => void
}) {
  const queryClient = useQueryClient()
  const [prefs, setPrefs] = useState<Prefs>(initial)
  const [error, setError] = useState<string | null>(null)

  const save = useMutation({
    mutationFn: () => api.put<Prefs>("/api/email-preferences", prefs),
    onSuccess: () => {
      toast.success("Email preferences saved")
      void queryClient.invalidateQueries({ queryKey: ["email-preferences"] })
      onSaved()
    },
    onError: (err) => setError(err instanceof Error ? err.message : "Failed"),
  })

  return (
    <>
      <div className="flex flex-col gap-4 py-2">
        {Object.entries(LABELS).map(([key, label]) => (
          <div key={key} className="flex items-center justify-between">
            <span className="text-sm">{label}</span>
            <Switch
              checked={prefs[key as keyof Prefs]}
              onCheckedChange={(v) => setPrefs((p) => ({ ...p, [key]: v }))}
              aria-label={label}
            />
          </div>
        ))}
      </div>
      {error && <p className="text-xs text-destructive">{error}</p>}
      <div className="flex justify-end gap-2">
        <Button variant="outline" onClick={onCancel}>
          Cancel
        </Button>
        <Button disabled={save.isPending} onClick={() => save.mutate()}>
          {save.isPending ? "Saving…" : "Save"}
        </Button>
      </div>
    </>
  )
}

export function EmailPreferencesDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const query = useQuery({
    queryKey: ["email-preferences"],
    queryFn: () => api.get<Prefs>("/api/email-preferences"),
    enabled: open,
  })

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>Email preferences</DialogTitle>
        </DialogHeader>
        {open && (
          <PrefsForm
            key={String(open)}
            initial={query.data ?? DEFAULTS}
            onSaved={() => onOpenChange(false)}
            onCancel={() => onOpenChange(false)}
          />
        )}
      </DialogContent>
    </Dialog>
  )
}

export function EmailPreferencesButton() {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button
        type="button"
        className="w-full px-2 py-1.5 text-left text-sm hover:bg-muted"
        onClick={() => setOpen(true)}
      >
        Email preferences
      </button>
      <EmailPreferencesDialog open={open} onOpenChange={setOpen} />
    </>
  )
}
