import { zodResolver } from "@hookform/resolvers/zod"
import { useForm } from "react-hook-form"
import { Navigate, useLocation, useNavigate } from "react-router"
import { z } from "zod"

import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { useAuth } from "@/lib/auth"
import { ApiError } from "@/lib/api/client"

const loginSchema = z.object({
  email: z.string().min(1, "Email is required").email("Enter a valid email"),
  password: z.string().min(1, "Password is required"),
})

type LoginForm = z.infer<typeof loginSchema>

export function LoginPage() {
  const { user, isLoading, login } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()

  const {
    register,
    handleSubmit,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<LoginForm>({ resolver: zodResolver(loginSchema) })

  if (!isLoading && user) {
    return <Navigate to={location.state?.from ?? "/"} replace />
  }

  const onSubmit = handleSubmit(async (values) => {
    try {
      await login(values.email, values.password)
      navigate(location.state?.from ?? "/", { replace: true })
    } catch (error) {
      const detail =
        error instanceof ApiError
          ? error.status === 401
            ? "Invalid email or password"
            : error.status === 404
              ? "Auth service is not available yet (it lands with plan-4)."
              : error.message
          : "Could not reach the API"
      setError("root", { message: detail })
    }
  })

  return (
    <div className="flex min-h-svh items-center justify-center p-4">
      <Card className="w-full max-w-sm">
        <CardHeader className="text-center">
          <CardTitle className="text-2xl font-semibold tracking-tight">Sign in to ERP</CardTitle>
          <CardDescription>Use your ERP account to continue</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={onSubmit} className="flex flex-col gap-4" noValidate>
            <div className="flex flex-col gap-2">
              <Label htmlFor="email">Email</Label>
              <Input
                id="email"
                type="email"
                autoComplete="email"
                placeholder="you@company.com"
                {...register("email")}
              />
              {errors.email && <p className="text-xs text-destructive">{errors.email.message}</p>}
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor="password">Password</Label>
              <Input
                id="password"
                type="password"
                autoComplete="current-password"
                placeholder="••••••••"
                {...register("password")}
              />
              {errors.password && (
                <p className="text-xs text-destructive">{errors.password.message}</p>
              )}
            </div>
            {errors.root && (
              <p className="rounded-md bg-destructive/10 px-3 py-2 text-xs text-destructive">
                {errors.root.message}
              </p>
            )}
            <Button type="submit" disabled={isSubmitting}>
              {isSubmitting ? "Signing in…" : "Sign in"}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}
