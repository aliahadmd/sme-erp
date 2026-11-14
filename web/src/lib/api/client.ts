/**
 * Typed fetch wrapper for the ERP API.
 *
 * Errors from the API always have the shape {"error": {"code", "detail", ...}} —
 * they are normalized into ApiError and can be caught anywhere in the app.
 */

const API_URL: string = import.meta.env.VITE_API_URL ?? "http://localhost:8000"

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly errors?: unknown[]

  constructor(
    status: number,
    code: string,
    detail: string,
    errors?: unknown[]
  ) {
    super(detail)
    this.name = "ApiError"
    this.status = status
    this.code = code
    this.errors = errors
  }
}

type Tokens = {
  get: () => string | null
  session: () => number
  clear: () => void
  refresh: () => Promise<boolean>
}

/**
 * Injected by the auth module so this file stays auth-agnostic.
 * Circular-free: auth imports client, client gets tokens via this hook.
 */
let tokenProvider: Tokens = {
  get: () => null,
  session: () => 0,
  clear: () => {},
  refresh: async () => false,
}

export function setTokenProvider(provider: Tokens) {
  tokenProvider = provider
}

export function captureApiSession() {
  const provider = tokenProvider
  const session = provider.session()
  return () => {
    if (provider !== tokenProvider || provider.session() !== session) {
      throw new ApiError(
        401,
        "session_changed",
        "Your session changed. Retry the action from the current account."
      )
    }
  }
}

export type MutationOptions = { retry?: boolean; idempotencyKey?: string }

export async function apiFetch<T>(
  path: string,
  options: RequestInit & { retry?: boolean } = {}
): Promise<T> {
  const { retry = true, headers, ...rest } = options
  const provider = tokenProvider
  const assertCurrentSession = captureApiSession()

  const finalHeaders = new Headers(headers)
  finalHeaders.set("Accept", "application/json")
  if (rest.body && !finalHeaders.has("Content-Type")) {
    finalHeaders.set("Content-Type", "application/json")
  }
  if (
    !["GET", "HEAD"].includes((rest.method ?? "GET").toUpperCase()) &&
    !finalHeaders.has("Idempotency-Key")
  ) {
    finalHeaders.set("Idempotency-Key", crypto.randomUUID())
  }
  const token = provider.get()
  if (token) {
    finalHeaders.set("Authorization", `Bearer ${token}`)
  }

  const response = await fetch(`${API_URL}${path}`, {
    ...rest,
    headers: finalHeaders,
    credentials: "include",
  })
  assertCurrentSession()

  if (
    response.status === 401 &&
    retry &&
    !path.startsWith("/api/auth/") &&
    provider.refresh
  ) {
    const refreshed = await provider.refresh()
    assertCurrentSession()
    if (refreshed) {
      return apiFetch<T>(path, {
        ...options,
        headers: finalHeaders,
        retry: false,
      })
    }
    provider.clear()
  }

  if (!response.ok) {
    let code = "http_error"
    let detail = `Request failed with status ${response.status}`
    let errors: unknown[] | undefined
    try {
      const body = (await response.json()) as {
        error?: Record<string, unknown>
      }
      if (body.error) {
        code = String(body.error.code ?? code)
        detail = String(body.error.detail ?? detail)
        errors = body.error.errors as unknown[] | undefined
      }
    } catch {
      // non-JSON error body — keep defaults
    }
    assertCurrentSession()
    throw new ApiError(response.status, code, detail, errors)
  }

  if (response.status === 204) {
    return undefined as T
  }
  const result = (await response.json()) as T
  assertCurrentSession()
  return result
}

export const api = {
  get: <T>(path: string) => apiFetch<T>(path),
  post: <T>(path: string, body?: unknown, options: MutationOptions = {}) =>
    apiFetch<T>(path, {
      method: "POST",
      body: body === undefined ? undefined : JSON.stringify(body),
      retry: options.retry,
      headers: options.idempotencyKey
        ? { "Idempotency-Key": options.idempotencyKey }
        : undefined,
    }),
  patch: <T>(path: string, body?: unknown, options: MutationOptions = {}) =>
    apiFetch<T>(path, {
      method: "PATCH",
      body: body === undefined ? undefined : JSON.stringify(body),
      retry: options.retry,
      headers: options.idempotencyKey
        ? { "Idempotency-Key": options.idempotencyKey }
        : undefined,
    }),
  put: <T>(path: string, body?: unknown, options: MutationOptions = {}) =>
    apiFetch<T>(path, {
      method: "PUT",
      body: body === undefined ? undefined : JSON.stringify(body),
      retry: options.retry,
      headers: options.idempotencyKey
        ? { "Idempotency-Key": options.idempotencyKey }
        : undefined,
    }),
  delete: <T>(path: string, options: MutationOptions = {}) =>
    apiFetch<T>(path, {
      method: "DELETE",
      retry: options.retry,
      headers: options.idempotencyKey
        ? { "Idempotency-Key": options.idempotencyKey }
        : undefined,
    }),
}
