import { createBrowserRouter, Navigate } from "react-router"

import { PlaceholderPage } from "@/app/placeholder-page"
import { RequireAuth } from "@/app/require-auth"
import { LoginPage } from "@/features/auth/login-page"
import { DashboardPage } from "@/features/dashboard/dashboard-page"

export const router = createBrowserRouter([
  { path: "/login", element: <LoginPage /> },
  {
    path: "/",
    element: <RequireAuth />,
    children: [
      { index: true, element: <DashboardPage /> },
      { path: "crm", element: <PlaceholderPage title="CRM" plan={5} /> },
      { path: "sales", element: <PlaceholderPage title="Sales" plan={6} /> },
      { path: "purchasing", element: <PlaceholderPage title="Purchasing" plan={6} /> },
      { path: "inventory", element: <PlaceholderPage title="Inventory" plan={7} /> },
      { path: "invoicing", element: <PlaceholderPage title="Invoicing" plan={8} /> },
      { path: "accounting", element: <PlaceholderPage title="Accounting" plan={9} /> },
      { path: "reports", element: <PlaceholderPage title="Reports" plan={10} /> },
      { path: "settings", element: <PlaceholderPage title="Settings" plan={4} /> },
    ],
  },
  { path: "*", element: <Navigate to="/" replace /> },
])
