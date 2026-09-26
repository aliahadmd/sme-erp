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
  /** Any one of these permissions grants the whole section. */
  anyPermission?: string[]
}

export const NAV_ITEMS: NavItem[] = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard },
  { to: "/crm", label: "CRM", icon: Users, anyPermission: ["crm.contact.read"] },
  { to: "/products", label: "Products", icon: Package, anyPermission: ["catalog.product.read"] },
  { to: "/sales", label: "Sales", icon: ShoppingCart, anyPermission: ["sales.order.read"] },
  { to: "/purchasing", label: "Purchasing", icon: Truck, anyPermission: ["purchasing.order.read"] },
  { to: "/inventory", label: "Inventory", icon: Boxes, anyPermission: ["inventory.stock.read"] },
  { to: "/invoicing", label: "Invoicing", icon: FileText, anyPermission: ["invoicing.invoice.read"] },
  { to: "/accounting", label: "Accounting", icon: Landmark, anyPermission: ["accounting.entry.read"] },
  { to: "/reports", label: "Reports", icon: BarChart3, anyPermission: ["reports.view"] },
  {
    to: "/settings",
    label: "Settings",
    icon: Settings,
    anyPermission: ["core.user.read", "core.role.read", "core.org.read", "core.settings.read", "core.audit.read"],
  },
]
