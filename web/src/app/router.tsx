import { createBrowserRouter, Navigate } from "react-router"

import { PlaceholderPage } from "@/app/placeholder-page"
import { RequireAuth } from "@/app/require-auth"
import { LoginPage } from "@/features/auth/login-page"
import { DashboardPage } from "@/features/dashboard/dashboard-page"
import { AuditPage as SettingsAuditPage } from "@/features/settings/audit-page"
import { SettingsLayout } from "@/features/settings/layout"
import { OrganizationPage as SettingsOrganizationPage } from "@/features/settings/organization-page"
import { RolesPage as SettingsRolesPage } from "@/features/settings/roles-page"
import { UsersPage as SettingsUsersPage } from "@/features/settings/users-page"
import { CatalogSettingsPage } from "@/features/products/catalog-settings-page"
import { ContactsPage } from "@/features/contacts/contacts-page"
import { ProductsPage } from "@/features/products/products-page"
import { OrderEditorRoute } from "@/features/documents/order-editor-page"
import { OrdersListPage } from "@/features/documents/orders-list-page"

export const router = createBrowserRouter([
  { path: "/login", element: <LoginPage /> },
  {
    path: "/",
    element: <RequireAuth />,
    children: [
      { index: true, element: <DashboardPage /> },
      { path: "crm", element: <ContactsPage /> },
      { path: "products", element: <ProductsPage /> },
      { path: "sales", element: <OrdersListPage module="sales" /> },
      { path: "sales/new", element: <OrderEditorRoute module="sales" /> },
      { path: "sales/:id", element: <OrderEditorRoute module="sales" /> },
      { path: "purchasing", element: <OrdersListPage module="purchasing" /> },
      { path: "purchasing/new", element: <OrderEditorRoute module="purchasing" /> },
      { path: "purchasing/:id", element: <OrderEditorRoute module="purchasing" /> },
      { path: "inventory", element: <PlaceholderPage title="Inventory" plan={7} /> },
      { path: "invoicing", element: <PlaceholderPage title="Invoicing" plan={8} /> },
      { path: "accounting", element: <PlaceholderPage title="Accounting" plan={9} /> },
      { path: "reports", element: <PlaceholderPage title="Reports" plan={10} /> },
      {
        path: "settings",
        element: <SettingsLayout />,
        children: [
          { index: true, element: <SettingsUsersPage /> },
          { path: "roles", element: <SettingsRolesPage /> },
          { path: "organization", element: <SettingsOrganizationPage /> },
          { path: "catalog", element: <CatalogSettingsPage /> },
          { path: "audit", element: <SettingsAuditPage /> },
        ],
      },
    ],
  },
  { path: "*", element: <Navigate to="/" replace /> },
])
