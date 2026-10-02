import { useQuery } from "@tanstack/react-query"
import { Archive, Plus, Search } from "lucide-react"
import { useState } from "react"
import { useSearchParams } from "react-router"
import { toast } from "sonner"

import { contactsApi, type Contact, type ContactInput } from "@/features/contacts/api"
import { CurrencySelect } from "@/components/currency-select"
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
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { ApiError } from "@/lib/api/client"
import { queryKeys } from "@/lib/query-keys"

function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : "Something went wrong"
}

const TABS = [
  { value: "all", label: "All" },
  { value: "customer", label: "Customers" },
  { value: "supplier", label: "Suppliers" },
]

export function ContactsPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const type = searchParams.get("type") ?? "all"
  const [search, setSearch] = useState("")
  const [appliedSearch, setAppliedSearch] = useState("")
  const [editing, setEditing] = useState<Contact | null>(null)
  const [creating, setCreating] = useState(false)

  const { data, isLoading, refetch } = useQuery({
    queryKey: queryKeys.contacts({ type, q: appliedSearch }),
    queryFn: () =>
      contactsApi.list({
        type: type === "all" ? undefined : type,
        q: appliedSearch,
        limit: 50,
      }),
  })

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Contacts</h1>
          <p className="text-sm text-muted-foreground">
            Customers and suppliers in one place — a company can be both
          </p>
        </div>
        <Button onClick={() => setCreating(true)}>
          <Plus className="size-4" /> New contact
        </Button>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <Tabs value={type} onValueChange={(v) => setSearchParams(v === "all" ? {} : { type: v })}>
          <TabsList>
            {TABS.map((tab) => (
              <TabsTrigger key={tab.value} value={tab.value}>
                {tab.label}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
        <form
          className="flex items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault()
            setAppliedSearch(search)
          }}
        >
          <div className="relative">
            <Search className="absolute top-2.5 left-2 size-4 text-muted-foreground" />
            <Input
              placeholder="Search name or code…"
              className="w-64 pl-8"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <Button variant="outline" type="submit">
            Search
          </Button>
        </form>
      </div>

      <div className="rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Code</TableHead>
              <TableHead>Name</TableHead>
              <TableHead>Type</TableHead>
              <TableHead>Terms</TableHead>
              <TableHead>Tags</TableHead>
              <TableHead className="w-16" />
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
                  No contacts yet — create your first one
                </TableCell>
              </TableRow>
            )}
            {data?.items.map((contact) => (
              <TableRow key={contact.id}>
                <TableCell className="font-mono text-xs">{contact.code}</TableCell>
                <TableCell className="font-medium">{contact.name}</TableCell>
                <TableCell>
                  <div className="flex gap-1">
                    {contact.is_customer && <Badge>customer</Badge>}
                    {contact.is_supplier && <Badge variant="secondary">supplier</Badge>}
                  </div>
                </TableCell>
                <TableCell className="text-sm">{contact.payment_terms_days} days</TableCell>
                <TableCell className="text-sm text-muted-foreground">
                  {contact.tags.join(", ") || "—"}
                </TableCell>
                <TableCell>
                  <div className="flex gap-1">
                    <Button variant="ghost" size="sm" onClick={() => setEditing(contact)}>
                      Edit
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label="Archive contact"
                      onClick={async () => {
                        try {
                          await contactsApi.archive(contact.id, true)
                          toast.success("Contact archived")
                          void refetch()
                        } catch (err) {
                          toast.error(errorMessage(err))
                        }
                      }}
                    >
                      <Archive className="size-4" />
                    </Button>
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      <ContactDialog
        key={editing?.id ?? "new"}
        open={creating || !!editing}
        contact={editing}
        onOpenChange={(open) => {
          if (!open) {
            setCreating(false)
            setEditing(null)
          }
        }}
        onDone={() => {
          void refetch()
          setCreating(false)
          setEditing(null)
        }}
      />
    </div>
  )
}

function ContactDialog({
  open,
  contact,
  onOpenChange,
  onDone,
}: {
  open: boolean
  contact: Contact | null
  onOpenChange: (open: boolean) => void
  onDone: () => void
}) {
  const [form, setForm] = useState<ContactInput>({
    name: contact?.name ?? "",
    is_customer: contact?.is_customer ?? true,
    is_supplier: contact?.is_supplier ?? false,
    legal_name: contact?.legal_name ?? "",
    tax_id: contact?.tax_id ?? "",
    // New contacts: omitted → the API applies the organization base currency.
    currency: contact?.currency,
    payment_terms_days: contact?.payment_terms_days ?? 30,
    credit_limit: contact?.credit_limit ?? "0",
    tags: contact?.tags ?? [],
    notes: contact?.notes ?? "",
  })
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  const save = async () => {
    setSaving(true)
    setError(null)
    try {
      if (contact) {
        await contactsApi.update(contact.id, form)
        toast.success("Contact updated")
      } else {
        await contactsApi.create(form)
        toast.success("Contact created")
      }
      onDone()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{contact ? `Edit ${contact.name}` : "New contact"}</DialogTitle>
        </DialogHeader>
        <div className="flex flex-col gap-4">
          <div className="flex flex-col gap-2">
            <Label htmlFor="contact-name">Name *</Label>
            <Input
              id="contact-name"
              value={form.name}
              onChange={(e) => setForm((p) => ({ ...p, name: e.target.value }))}
            />
          </div>
          <div className="flex gap-4">
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                className="size-4 accent-primary"
                checked={form.is_customer}
                onChange={(e) => setForm((p) => ({ ...p, is_customer: e.target.checked }))}
              />
              Customer
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                className="size-4 accent-primary"
                checked={form.is_supplier}
                onChange={(e) => setForm((p) => ({ ...p, is_supplier: e.target.checked }))}
              />
              Supplier
            </label>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div className="flex flex-col gap-2">
              <Label htmlFor="contact-tax">Tax ID</Label>
              <Input
                id="contact-tax"
                value={form.tax_id ?? ""}
                onChange={(e) => setForm((p) => ({ ...p, tax_id: e.target.value }))}
              />
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor="contact-terms">Payment terms (days)</Label>
              <Input
                id="contact-terms"
                type="number"
                value={form.payment_terms_days}
                onChange={(e) =>
                  setForm((p) => ({ ...p, payment_terms_days: Number(e.target.value) }))
                }
              />
            </div>
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor="contact-currency">Default document currency</Label>
            <CurrencySelect
              id="contact-currency"
              value={form.currency}
              onChange={(code) => setForm((p) => ({ ...p, currency: code }))}
            />
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor="contact-tags">Tags (comma separated)</Label>
            <Input
              id="contact-tags"
              placeholder="wholesale, vip"
              value={form.tags?.join(", ") ?? ""}
              onChange={(e) =>
                setForm((p) => ({
                  ...p,
                  tags: e.target.value
                    .split(",")
                    .map((t) => t.trim())
                    .filter(Boolean),
                }))
              }
            />
          </div>
          {error && <p className="text-xs text-destructive">{error}</p>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button disabled={saving || !form.name} onClick={() => void save()}>
            {saving ? "Saving…" : contact ? "Save" : "Create"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
