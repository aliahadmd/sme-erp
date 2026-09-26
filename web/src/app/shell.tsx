import { LogOut, Menu } from "lucide-react"
import { useState } from "react"
import { NavLink, Outlet } from "react-router"
import { toast } from "sonner"

import { NAV_ITEMS } from "@/app/nav-items"
import { NotificationsBell } from "@/components/notifications-bell"
import { ThemeToggle } from "@/components/theme-toggle"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Separator } from "@/components/ui/separator"
import { EmailPreferencesDialog } from "@/features/settings/email-preferences"
import { Sheet, SheetContent, SheetTrigger } from "@/components/ui/sheet"
import { useAuth } from "@/lib/auth"

function NavLinks({ onNavigate }: { onNavigate?: () => void }) {
  const { hasAnyPermission } = useAuth()
  const visible = NAV_ITEMS.filter(
    (item) => !item.anyPermission || hasAnyPermission(item.anyPermission),
  )
  return (
    <nav className="flex flex-col gap-1 px-2">
      {visible.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          end={item.to === "/"}
          onClick={onNavigate}
          className={({ isActive }) =>
            `flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors ${
              isActive
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:bg-muted hover:text-foreground"
            }`
          }
        >
          <item.icon className="size-4 shrink-0" />
          {item.label}
        </NavLink>
      ))}
    </nav>
  )
}

function Brand() {
  return (
    <div className="flex h-14 items-center px-4">
      <span className="text-lg font-semibold tracking-tight">ERP</span>
      <span className="ml-2 rounded bg-muted px-1.5 py-0.5 text-[10px] font-medium uppercase text-muted-foreground">
        SME
      </span>
    </div>
  )
}

function UserMenu() {
  const { user, logout } = useAuth()
  const [prefsOpen, setPrefsOpen] = useState(false)
  if (!user) return null
  const initials = user.full_name
    .split(" ")
    .map((part) => part[0])
    .slice(0, 2)
    .join("")
    .toUpperCase()

  return (
    <>
    <EmailPreferencesDialog open={prefsOpen} onOpenChange={setPrefsOpen} />
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <Button variant="ghost" className="h-9 gap-2 px-2">
            <Avatar className="size-7">
              <AvatarFallback className="text-xs">{initials || "?"}</AvatarFallback>
            </Avatar>
            <span className="hidden text-sm font-medium sm:inline">{user.full_name}</span>
          </Button>
        }
      />
      <DropdownMenuContent align="end" className="w-56">
        <DropdownMenuLabel>
          <div className="text-sm font-medium">{user.full_name}</div>
          <div className="text-xs font-normal text-muted-foreground">{user.email}</div>
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem onClick={() => setPrefsOpen(true)}>
          Email preferences
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem
          onClick={() => {
            void logout().then(() => toast.success("Signed out"))
          }}
        >
          <LogOut className="size-4" /> Sign out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
    </>
  )
}

export function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-svh">
      {/* Desktop sidebar */}
      <aside className="hidden w-60 shrink-0 border-r bg-sidebar md:flex md:flex-col">
        <Brand />
        <Separator />
        <div className="flex-1 py-3">
          <NavLinks />
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 items-center gap-2 border-b px-4">
          {/* Mobile nav */}
          <Sheet>
            <SheetTrigger
              render={
                <Button variant="ghost" size="icon" className="md:hidden" aria-label="Open menu" />
              }
            >
              <Menu className="size-5" />
            </SheetTrigger>
            <SheetContent side="left" className="w-64 p-0">
              <Brand />
              <Separator />
              <div className="py-3">
                <NavLinks />
              </div>
            </SheetContent>
          </Sheet>
          <div className="flex-1" />
          <NotificationsBell />
          <ThemeToggle />
          <UserMenu />
        </header>
        <main className="flex-1 p-4 md:p-6">{children}</main>
      </div>
    </div>
  )
}

export { Outlet as ShellOutlet }
