/**
 * Auth state: access token lives in memory only; the refresh token is an
 * httpOnly cookie set by the API. A 401 triggers one silent refresh attempt.
 */
/* eslint-disable react-refresh/only-export-components */
import * as React from "react"

import { api, ApiError, setTokenProvider } from "@/lib/api/client"

export type CurrentUser = {
  id: string
  email: string
  full_name: string
  is_superuser: boolean
  roles: string[]
  permissions: string[]
}

let accessToken: string | null = null
let sessionGeneration = 0

export function getAccessToken() {
  return accessToken
}

/** Explicit session replacement; ordinary token rotation preserves the generation. */
export function setAccessToken(token: string | null) {
  sessionGeneration += 1
  accessToken = token
}

type AuthState = {
  user: CurrentUser | null
  isLoading: boolean
  login: (email: string, password: string) => Promise<void>
  logout: () => Promise<void>
  hasPermission: (code: string) => boolean
  hasAnyPermission: (codes: string[]) => boolean
}

const AuthContext = React.createContext<AuthState | undefined>(undefined)

let refreshInFlight: { session: number; promise: Promise<boolean> } | null =
  null

async function doRefresh(session: number): Promise<boolean> {
  try {
    // retry: false is critical — a 401 from the refresh call itself must not
    // re-enter the refresh flow (that recursion caused infinite request storms).
    const result = await api.post<{ access_token: string }>(
      "/api/auth/refresh",
      undefined,
      {
        retry: false,
      }
    )
    if (session !== sessionGeneration) return false
    accessToken = result.access_token
    return true
  } catch {
    if (session === sessionGeneration) accessToken = null
    return false
  }
}

/** Single-flight: parallel 401s share one refresh instead of racing the
 * rotating refresh cookie against itself. */
function requestRefresh(): Promise<boolean> {
  if (!refreshInFlight || refreshInFlight.session !== sessionGeneration) {
    const flight = {
      session: sessionGeneration,
      promise: Promise.resolve(false),
    }
    flight.promise = doRefresh(flight.session).finally(() => {
      if (refreshInFlight === flight) refreshInFlight = null
    })
    refreshInFlight = flight
  }
  return refreshInFlight.promise
}

// Repeated mount effects must retain the same provider while sharing a refresh.
const sessionTokens = {
  get: () => accessToken,
  session: () => sessionGeneration,
  clear: () => {
    accessToken = null
  },
  refresh: requestRefresh,
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = React.useState<CurrentUser | null>(null)
  const [isLoading, setIsLoading] = React.useState(true)

  React.useEffect(() => {
    setTokenProvider(sessionTokens)
  }, [])

  const restoreSession = React.useCallback(async () => {
    const session = sessionGeneration
    try {
      if (!accessToken) {
        const refreshed = await requestRefresh()
        if (!refreshed) return
      }
      const me = await api.get<CurrentUser>("/api/auth/me")
      if (session === sessionGeneration) setUser(me)
    } catch {
      if (session === sessionGeneration) {
        accessToken = null
        setUser(null)
      }
    } finally {
      if (session === sessionGeneration) setIsLoading(false)
    }
  }, [])

  React.useEffect(() => {
    // Session restore on mount — setState happens in async callbacks only.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void restoreSession()
  }, [restoreSession])

  const login = React.useCallback(async (email: string, password: string) => {
    const session = ++sessionGeneration
    accessToken = null
    setUser(null)
    setIsLoading(true)
    try {
      const result = await api.post<{
        access_token: string
        token_type: string
      }>("/api/auth/login", { email, password })
      if (session !== sessionGeneration)
        throw new ApiError(
          401,
          "session_changed",
          "Your session changed. Retry the action from the current account."
        )
      accessToken = result.access_token
      const me = await api.get<CurrentUser>("/api/auth/me")
      if (session !== sessionGeneration)
        throw new ApiError(
          401,
          "session_changed",
          "Your session changed. Retry the action from the current account."
        )
      setUser(me)
    } catch (error) {
      if (session === sessionGeneration) accessToken = null
      if (error instanceof ApiError) throw error
      throw new ApiError(0, "network_error", "Could not reach the API")
    } finally {
      if (session === sessionGeneration) setIsLoading(false)
    }
  }, [])

  const logout = React.useCallback(async () => {
    const session = ++sessionGeneration
    try {
      await api.post("/api/auth/logout")
    } catch {
      // Server revocation is best-effort; clear only this logout's own session.
    }
    if (session === sessionGeneration) {
      setAccessToken(null)
      setUser(null)
      setIsLoading(false)
    }
  }, [])

  const hasPermission = React.useCallback(
    (code: string) => {
      if (!user) return false
      if (user.is_superuser) return true
      return user.permissions.includes(code)
    },
    [user]
  )

  const hasAnyPermission = React.useCallback(
    (codes: string[]) => {
      if (!user) return false
      if (user.is_superuser) return true
      return codes.some((code) => user.permissions.includes(code))
    },
    [user]
  )

  const value = React.useMemo(
    () => ({ user, isLoading, login, logout, hasPermission, hasAnyPermission }),
    [user, isLoading, login, logout, hasPermission, hasAnyPermission]
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const context = React.useContext(AuthContext)
  if (!context) {
    throw new Error("useAuth must be used within an AuthProvider")
  }
  return context
}
