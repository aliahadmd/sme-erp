/**
 * Money helpers. API amounts are decimal STRINGS — never add them as floats
 * (0.1 + 0.2 !== 0.3). Sums are computed in integer cents with BigInt.
 */

function toCents(value: string | number | null | undefined): bigint {
  const text = String(value ?? "0").trim() || "0"
  const negative = text.startsWith("-")
  const [whole, fraction = ""] = text.replace(/^[-+]/, "").split(".")
  const cents = BigInt(whole || "0") * 100n + BigInt((fraction + "00").slice(0, 2))
  // Round half-up on the third decimal (amounts are 2dp; this guards 4dp input).
  const roundUp = Number(fraction[2] ?? "0") >= 5 ? 1n : 0n
  const magnitude = cents + roundUp
  return negative ? -magnitude : magnitude
}

function fromCents(cents: bigint): string {
  const negative = cents < 0n
  const abs = negative ? -cents : cents
  const whole = abs / 100n
  const fraction = (abs % 100n).toString().padStart(2, "0")
  return `${negative ? "-" : ""}${whole}.${fraction}`
}

/** Exact sum of decimal amounts, formatted with 2 decimals. */
export function sumMoney(values: Array<string | number | null | undefined>): string {
  return fromCents(values.reduce<bigint>((total, value) => total + toCents(value), 0n))
}

/** Exact a − b, formatted with 2 decimals. */
export function subtractMoney(a: string | number, b: string | number): string {
  return fromCents(toCents(a) - toCents(b))
}

/** Format a decimal amount with 2 decimals (display only). */
export function formatMoney(value: string | number | null | undefined): string {
  return fromCents(toCents(value))
}

/** Decimal amount → integer cents (exact). */
export function moneyToCents(value: string | number | null | undefined): bigint {
  return toCents(value)
}

/** Integer cents → decimal string with 2 decimals. */
export function centsToMoney(cents: bigint): string {
  return fromCents(cents)
}

/**
 * FIFO allocation of an amount over open balances, in exact cents.
 * Returns only the items that receive a positive share.
 */
export function allocateFifo<T>(
  amount: string,
  items: T[],
  balanceOf: (item: T) => string,
): { item: T; amount: string }[] {
  let left = toCents(amount)
  const out: { item: T; amount: string }[] = []
  for (const item of items) {
    if (left <= 0n) break
    const balance = toCents(balanceOf(item))
    const take = balance < left ? balance : left
    if (take > 0n) {
      out.push({ item, amount: fromCents(take) })
      left -= take
    }
  }
  return out
}

/** Parse a decimal string into an integer scaled by 10^scale (truncating extra digits). */
function scaled(value: string | number | null | undefined, scale: number): bigint {
  const text = String(value ?? "0").trim() || "0"
  const negative = text.startsWith("-")
  const [whole, fraction = ""] = text.replace(/^[-+]/, "").split(".")
  const digits = BigInt((whole || "0") + (fraction + "0".repeat(scale)).slice(0, scale))
  return negative ? -digits : digits
}

/** n / d rounded half-to-even (banker's rounding), like Python's ROUND_HALF_EVEN. */
function divHalfEven(n: bigint, d: bigint): bigint {
  const negative = n < 0n !== d < 0n
  const an = n < 0n ? -n : n
  const ad = d < 0n ? -d : d
  let q = an / ad
  const twice = (an % ad) * 2n
  if (twice > ad || (twice === ad && q % 2n === 1n)) q += 1n
  return negative ? -q : q
}

export type LineCents = { gross: bigint; base: bigint; tax: bigint }

/**
 * Exact mirror of the API's line math (app/shared/totals.py): quantities
 * 4dp, unit prices 6dp, percentages 2dp, money 2dp with banker's rounding.
 *   gross = qty × price            base = qty × price × (1 − discount%)
 *   tax   = base × rate%           (each rounded to cents)
 */
export function lineMathCents(
  qty: string | number,
  unitPrice: string | number,
  discountPct: string | number = "0",
  taxRatePct: string | number = "0",
): LineCents {
  const q = scaled(qty, 4)
  const p = scaled(unitPrice, 6)
  const keep = 10000n - scaled(discountPct, 2) // (100 − discount%) × 100
  const gross = divHalfEven(q * p, 10n ** 8n)
  const base = divHalfEven(q * p * keep, 10n ** 12n)
  const tax = divHalfEven(base * scaled(taxRatePct, 2), 10n ** 4n)
  return { gross, base, tax }
}
