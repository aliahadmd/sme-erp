import { describe, expect, it, vi } from "vitest"

import { ApiError, apiFetch, setTokenProvider } from "./client"

describe("apiFetch error normalization", () => {
  it("throws ApiError with the API error envelope", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        () =>
          Promise.resolve(
            new Response(JSON.stringify({ error: { code: "forbidden", detail: "Nope" } }), {
              status: 403,
              headers: { "Content-Type": "application/json" },
            }),
          ) as unknown as Response,
      ),
    )
    setTokenProvider({ get: () => null, clear: () => {}, refresh: async () => false })

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
      vi.fn(() => Promise.resolve(new Response("boom", { status: 500 })) as unknown as Response),
    )
    setTokenProvider({ get: () => null, clear: () => {}, refresh: async () => false })

    await expect(apiFetch("/api/anything")).rejects.toMatchObject({
      name: "ApiError",
      status: 500,
      code: "http_error",
    })
    vi.unstubAllGlobals()
  })

  it("ApiError carries validation errors list", () => {
    const err = new ApiError(422, "validation_error", "Bad", [{ loc: ["email"] }])
    expect(err.errors).toEqual([{ loc: ["email"] }])
  })
})
