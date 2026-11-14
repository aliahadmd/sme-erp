import { describe, expect, it, vi } from "vitest"

import { ApiError, apiFetch, setTokenProvider } from "./client"

describe("apiFetch error normalization", () => {
  it("keeps one mutation key through refresh and uses a new key for the next action", async () => {
    let token = "expired"
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(new Response("{}", { status: 401 }))
      .mockResolvedValueOnce(new Response("{}", { status: 200 }))
      .mockResolvedValueOnce(new Response("{}", { status: 200 }))
    vi.stubGlobal("fetch", fetcher)
    setTokenProvider({
      get: () => token,
      session: () => 0,
      clear: () => {},
      refresh: async () => {
        token = "fresh"
        return true
      },
    })
    await apiFetch("/api/crm/contacts", { method: "POST", body: "{}" })
    await apiFetch("/api/crm/contacts", { method: "POST", body: "{}" })
    const first = fetcher.mock.calls[0][1].headers as Headers
    const retry = fetcher.mock.calls[1][1].headers as Headers
    const next = fetcher.mock.calls[2][1].headers as Headers
    expect(first.get("Idempotency-Key")).toBeTruthy()
    expect(retry.get("Idempotency-Key")).toBe(first.get("Idempotency-Key"))
    expect(next.get("Idempotency-Key")).not.toBe(first.get("Idempotency-Key"))
    expect(retry.get("Authorization")).toBe("Bearer fresh")
    vi.unstubAllGlobals()
  })

  it("throws ApiError with the API error envelope", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        () =>
          Promise.resolve(
            new Response(
              JSON.stringify({ error: { code: "forbidden", detail: "Nope" } }),
              {
                status: 403,
                headers: { "Content-Type": "application/json" },
              }
            )
          ) as unknown as Response
      )
    )
    setTokenProvider({
      get: () => null,
      session: () => 0,
      clear: () => {},
      refresh: async () => false,
    })

    await expect(apiFetch("/api/anything")).rejects.toMatchObject({
      name: "ApiError",
      status: 403,
      code: "forbidden",
      message: "Nope",
    })
    vi.unstubAllGlobals()
  })

  it("survives non-JSON error bodies", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        () =>
          Promise.resolve(
            new Response("boom", { status: 500 })
          ) as unknown as Response
      )
    )
    setTokenProvider({
      get: () => null,
      session: () => 0,
      clear: () => {},
      refresh: async () => false,
    })

    await expect(apiFetch("/api/anything")).rejects.toMatchObject({
      name: "ApiError",
      status: 500,
      code: "http_error",
    })
    vi.unstubAllGlobals()
  })

  it("ApiError carries validation errors list", () => {
    const err = new ApiError(422, "validation_error", "Bad", [
      { loc: ["email"] },
    ])
    expect(err.errors).toEqual([{ loc: ["email"] }])
  })
})
