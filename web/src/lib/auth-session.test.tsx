import { act, cleanup, render, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, expect, it, vi } from "vitest"
import { StrictMode, useEffect } from "react"

import { api } from "@/lib/api/client"
import {
  AuthProvider,
  getAccessToken,
  setAccessToken,
  useAuth,
} from "@/lib/auth"

let auth: ReturnType<typeof useAuth>
function Probe() {
  const value = useAuth()
  useEffect(() => {
    auth = value
  }, [value])
  return null
}
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => {
    resolve = done
  })
  return { promise, resolve }
}

beforeEach(() => setAccessToken(null))
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

it.each(["response", "refresh"])(
  "does not replay an old account's mutation when its %s finishes after an account switch",
  async (mode) => {
    let account = ""
    const oldResponse = deferred<Response>()
    const oldRefresh = deferred<Response>()
    const mutations: string[] = []
    let refreshing = false
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, options: RequestInit = {}) => {
        const path = new URL(url).pathname
        if (path === "/api/auth/login") {
          account = JSON.parse(options.body as string).email.split("@")[0]
          return Response.json({ access_token: account + "-token" })
        }
        if (path === "/api/auth/me")
          return Response.json({
            id: account,
            full_name: account,
            email: account + "@example.com",
            roles: [account],
            permissions: [],
            is_superuser: account === "admin",
          })
        if (path === "/api/auth/logout") {
          account = ""
          return Response.json({ status: "ok" })
        }
        if (path === "/api/auth/refresh") {
          if (!account) return new Response("{}", { status: 401 })
          refreshing = true
          return mode === "refresh"
            ? oldRefresh.promise
            : Response.json({ access_token: account + "-refreshed" })
        }
        if (path === "/api/invoicing/payments") {
          mutations.push(
            new Headers(options.headers).get("Authorization") ?? ""
          )
          if (mutations.length === 1)
            return mode === "response"
              ? oldResponse.promise
              : new Response("{}", { status: 401 })
          return Response.json({ id: "unexpected-replayed-payment" })
        }
        throw new Error("Unexpected request: " + path)
      })
    )
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>
    )
    await waitFor(() => expect(auth.isLoading).toBe(false))
    await act(() => auth.login("admin@example.com", "test-password"))
    const outcome = api
      .post("/api/invoicing/payments", { amount: "1.00" })
      .then(
        (value) => ({ value, error: null }),
        (error) => ({ value: null, error })
      )
    if (mode === "refresh") await waitFor(() => expect(refreshing).toBe(true))
    await act(() => auth.logout())
    await act(() => auth.login("accountant@example.com", "test-password"))
    oldResponse.resolve(new Response("{}", { status: 401 }))
    oldRefresh.resolve(Response.json({ access_token: "admin-refreshed" }))
    const result = await outcome
    expect(mutations).toEqual(["Bearer admin-token"])
    expect(result.error).toMatchObject({ code: "session_changed" })
    expect(auth.user?.id).toBe("accountant")
    expect(getAccessToken()).toBe("accountant-token")
  }
)

it("restores one rotating refresh cookie when Strict Mode repeats the mount effects", async () => {
  let refreshes = 0
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      const path = new URL(url).pathname
      if (path === "/api/auth/refresh") {
        refreshes += 1
        return Response.json({ access_token: "restored-admin" })
      }
      if (path === "/api/auth/me")
        return Response.json({
          id: "admin",
          full_name: "Admin",
          roles: [],
          permissions: [],
          is_superuser: true,
        })
      throw new Error("Unexpected request: " + path)
    })
  )
  render(
    <StrictMode>
      <AuthProvider>
        <Probe />
      </AuthProvider>
    </StrictMode>
  )
  await waitFor(() => expect(auth.user?.id).toBe("admin"))
  expect(refreshes).toBe(1)
  expect(getAccessToken()).toBe("restored-admin")
})

it("shares one refresh for concurrent commands within the same session and retains each mutation key", async () => {
  setAccessToken("expired")
  const refresh = deferred<Response>()
  let refreshes = 0
  const commands: { key: string; authorization: string }[] = []
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, options: RequestInit = {}) => {
      const path = new URL(url).pathname
      if (path === "/api/auth/me")
        return Response.json({
          id: "admin",
          full_name: "Admin",
          roles: [],
          permissions: [],
          is_superuser: true,
        })
      if (path === "/api/auth/refresh") {
        refreshes += 1
        return refresh.promise
      }
      if (path === "/api/invoicing/payments") {
        const headers = new Headers(options.headers)
        const authorization = headers.get("Authorization") ?? ""
        commands.push({
          key: headers.get("Idempotency-Key") ?? "",
          authorization,
        })
        return authorization === "Bearer expired"
          ? new Response("{}", { status: 401 })
          : Response.json({ status: "recorded" })
      }
      throw new Error("Unexpected request: " + path)
    })
  )
  render(
    <AuthProvider>
      <Probe />
    </AuthProvider>
  )
  await waitFor(() => expect(auth.user?.id).toBe("admin"))
  const pending = Promise.all([
    api.post("/api/invoicing/payments", { amount: "1.00" }),
    api.post("/api/invoicing/payments", { amount: "2.00" }),
  ])
  await waitFor(() => expect(refreshes).toBe(1))
  refresh.resolve(Response.json({ access_token: "fresh" }))
  await pending
  expect(refreshes).toBe(1)
  expect(commands).toHaveLength(4)
  const keys = new Set(commands.map((command) => command.key))
  expect(keys.size).toBe(2)
  for (const key of keys) {
    expect(key).toBeTruthy()
    expect(
      commands
        .filter((command) => command.key === key)
        .map((command) => command.authorization)
    ).toEqual(["Bearer expired", "Bearer fresh"])
  }
})
