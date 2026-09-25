import { NavLink, Outlet } from "react-router"

const TABS = [
  { to: "/inventory", label: "Stock", end: true },
  { to: "/inventory/receipts", label: "Receipts", end: false },
  { to: "/inventory/deliveries", label: "Deliveries", end: false },
  { to: "/inventory/adjustments", label: "Adjustments", end: false },
]

export function InventoryLayout() {
  return (
    <div className="flex flex-col gap-6">
      <nav className="flex gap-1 border-b">
        {TABS.map((tab) => (
          <NavLink
            key={tab.to}
            to={tab.to}
            end={tab.end}
            className={({ isActive }) =>
              `-mb-px border-b-2 px-3 py-2 text-sm transition-colors ${
                isActive
                  ? "border-primary font-medium text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              }`
            }
          >
            {tab.label}
          </NavLink>
        ))}
      </nav>
      <Outlet />
    </div>
  )
}
