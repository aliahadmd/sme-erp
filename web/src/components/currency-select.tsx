import { useQuery } from "@tanstack/react-query"

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { api } from "@/lib/api/client"

type Currency = { id: string; code: string; name: string }

const DEFAULT = "__default__"

/**
 * Document currency picker. "Default" sends no currency so the API applies
 * its rule (source document → party currency → organization base currency).
 * Any other choice must have an FX rate on the document date.
 */
export function CurrencySelect({
  value,
  onChange,
  id,
  disabled,
}: {
  value: string | undefined
  onChange: (code: string | undefined) => void
  id?: string
  disabled?: boolean
}) {
  const currencies = useQuery({
    queryKey: ["currencies", "list"],
    queryFn: () => api.get<Currency[]>("/api/currencies"),
  })
  const org = useQuery({
    queryKey: ["currencies", "org-base"],
    queryFn: () => api.get<{ base_currency: string }>("/api/org").catch(() => null),
  })
  const base = org.data?.base_currency
  const codes = new Set<string>((currencies.data ?? []).map((c) => c.code))
  if (base) codes.add(base)
  if (value) codes.add(value)

  return (
    <Select
      value={value ?? DEFAULT}
      onValueChange={(v) => onChange(!v || v === DEFAULT ? undefined : String(v))}
      disabled={disabled}
    >
      <SelectTrigger id={id}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={DEFAULT}>Default (party / base currency)</SelectItem>
        {[...codes].sort().map((code) => (
          <SelectItem key={code} value={code}>
            {code}
            {code === base ? " — base" : ""}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}
