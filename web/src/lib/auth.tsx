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

export function getAccessToken() {
  return accessToken
}

export function setAccessToken(token: string | null) {
  accessToken = token
}

type AuthState = {
  user: CurrentUser | null
  isLoading: boolean
  login: (email: string, password: string) => Promise<void>
  logout: () => Promise<void>
  hasPermission: (code: string) => boolean
}

const AuthContext = React.createContext<AuthState | undefined>(undefined)

async function requestRefresh(): Promise<boolean> {
  try {
    const result = await api.post<{ access_token: string }>("/api/auth/refresh")
    accessToken = result.access_token
    return true
  } catch {
    return false
  }
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = React.useState<CurrentUser | null>(null)
  const [isLoading, setIsLoading] = React.useState(true)

  React.useEffect(() => {
    setTokenProvider({
      get: () => accessToken,
      clear: () => {
        accessToken = null
      },
      refresh: requestRefresh,
    })
  }, [])

  const restoreSession = React.useCallback(async () => {
    try {
      if (!accessToken) {
        const refreshed = await requestRefresh()
        if (!refreshed) return
      }
      const me = await api.get<CurrentUser>("/api/auth/me")
      setUser(me)
    } catch {
      accessToken = null
      setUser(null)
    } finally {
      setIsLoading(false)
    }
  }, [])

  React.useEffect(() => {
    // Session restore on mount — setState happens in async callbacks only.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void restoreSession()
  }, [restoreSession])

  const login = React.useCallback(async (email: string, password: string) => {
    const result = await api.post<{ access_token: string; token_type: string }>(
      "/api/auth/login",
      { email, password },
    )
    accessToken = result.access_token
    try {
      const me = await api.get<CurrentUser>("/api/auth/me")
      setUser(me)
    } catch (error) {
      accessToken = null
      if (error instanceof ApiError) throw error
      throw new ApiError(0, "network_error", "Could not reach the API")
    }
  }, [])

  const logout = React.useCallback(async () => {
    try {
      await api.post("/api/auth/logout")
    } catch {
      // best-effort — clear locally regardless
    }
    accessToken = null
    setUser(null)
  }, [])

  const hasPermission = React.useCallback(
    (code: string) => {
      if (!user) return false
      if (user.is_superuser) return true
      return user.permissions.includes(code)
    },
    [user],
  )

  const value = React.useMemo(
    () => ({ user, isLoading, login, logout, hasPermission }),
    [user, isLoading, login, logout, hasPermission],
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
