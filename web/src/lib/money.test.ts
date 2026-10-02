import { describe, expect, it } from "vitest"

import { centsToMoney, formatMoney, lineMathCents, subtractMoney, sumMoney } from "@/lib/money"

describe("money helpers", () => {
  it("sums decimal strings exactly", () => {
    expect(sumMoney(["0.10", "0.20"])).toBe("0.30")
    expect(sumMoney(["1000000.01", "0.99", "-0.50"])).toBe("1000000.50")
    expect(sumMoney([])).toBe("0.00")
  })

  it("subtracts and formats", () => {
    expect(subtractMoney("100.00", "100.00")).toBe("0.00")
    expect(subtractMoney("10", "12.5")).toBe("-2.50")
    expect(formatMoney("12.3")).toBe("12.30")
    expect(formatMoney(null)).toBe("0.00")
  })
})

describe("lineMathCents mirrors the server's banker's rounding", () => {
  it("rounds half to even", () => {
    // 0.125 → 0.12 (even), 0.135 → 0.14 (even)
    expect(centsToMoney(lineMathCents("1", "0.125").base)).toBe("0.12")
    expect(centsToMoney(lineMathCents("1", "0.135").base)).toBe("0.14")
  })

  it("applies discount then tax", () => {
    const line = lineMathCents("3", "16.67", "10", "7")
    expect(centsToMoney(line.gross)).toBe("50.01")
    expect(centsToMoney(line.base)).toBe("45.01") // 50.01 × 0.9 = 45.009
    expect(centsToMoney(line.tax)).toBe("3.15") // 45.01 × 7% = 3.1507
  })

  it("avoids float drift", () => {
    expect(centsToMoney(lineMathCents("0.1", "3").base)).toBe("0.30")
  })
})
