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
  {
    to: "/",
    label: "Dashboard",
    icon: LayoutDashboard,
    anyPermission: ["reports.view"],
  },
  {
    to: "/crm",
    label: "CRM",
    icon: Users,
    anyPermission: ["crm.contact.read"],
  },
  {
    to: "/products",
    label: "Products",
    icon: Package,
    anyPermission: ["catalog.product.read", "catalog.product.update"],
  },
  {
    to: "/sales",
    label: "Sales",
    icon: ShoppingCart,
    anyPermission: ["sales.order.read", "sales.quote.read"],
  },
  {
    to: "/purchasing",
    label: "Purchasing",
    icon: Truck,
    anyPermission: ["purchasing.order.read"],
  },
  {
    to: "/inventory",
    label: "Inventory",
    icon: Boxes,
    anyPermission: [
      "inventory.stock.read",
      "inventory.receipt.read",
      "inventory.delivery.read",
      "inventory.adjustment.read",
    ],
  },
  {
    to: "/invoicing",
    label: "Invoicing",
    icon: FileText,
    anyPermission: ["invoicing.invoice.read", "invoicing.payment.read"],
  },
  {
    to: "/accounting",
    label: "Accounting",
    icon: Landmark,
    anyPermission: ["accounting.account.read", "accounting.entry.read"],
  },
  {
    to: "/reports",
    label: "Reports",
    icon: BarChart3,
    anyPermission: ["reports.view"],
  },
  {
    to: "/hr",
    label: "HR",
    icon: Users,
    anyPermission: ["hr.employee.read", "hr.leave.request"],
  },
  {
    to: "/settings",
    label: "Settings",
    icon: Settings,
    anyPermission: [
      "core.user.read",
      "core.role.read",
      "core.org.read",
      "core.settings.read",
      "core.audit.read",
      "catalog.tax.read",
      "catalog.uom.read",
      "catalog.category.read",
    ],
  },
]
