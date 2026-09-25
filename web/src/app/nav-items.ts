import {
  BarChart3,
  Boxes,
  Package,
  FileText,
  Landmark,
  LayoutDashboard,
  type LucideIcon,
  Settings,
  ShoppingCart,
  Truck,
  Users,
} from "lucide-react"

type NavItem = {
  to: string
  label: string
  icon: LucideIcon
}

export const NAV_ITEMS: NavItem[] = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard },
  { to: "/crm", label: "CRM", icon: Users },
  { to: "/products", label: "Products", icon: Package },
  { to: "/sales", label: "Sales", icon: ShoppingCart },
  { to: "/purchasing", label: "Purchasing", icon: Truck },
  { to: "/inventory", label: "Inventory", icon: Boxes },
  { to: "/invoicing", label: "Invoicing", icon: FileText },
  { to: "/accounting", label: "Accounting", icon: Landmark },
  { to: "/reports", label: "Reports", icon: BarChart3 },
  { to: "/settings", label: "Settings", icon: Settings },
]
