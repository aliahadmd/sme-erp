import { createBrowserRouter, Navigate } from "react-router"

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
import {
  AdjustmentsPage,
  DeliveriesPage,
  ReceiptsPage,
  StockPage,
} from "@/features/inventory/inventory-pages"
import { InventoryLayout } from "@/features/inventory/layout"
import { InvoicingLayout } from "@/features/invoicing/layout"
import { InvoicesPage } from "@/features/invoicing/invoices-page"
import { PaymentsPage } from "@/features/invoicing/payments-page"
import { AccountingLayout } from "@/features/accounting/layout"
import { AccountsPage } from "@/features/accounting/accounts-page"
import { JournalPage, TrialBalancePage } from "@/features/accounting/journal-and-trial-page"
import { ReportsPage } from "@/features/reports/reports-page"
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
      {
        path: "inventory",
        element: <InventoryLayout />,
        children: [
          { index: true, element: <StockPage /> },
          { path: "receipts", element: <ReceiptsPage /> },
          { path: "deliveries", element: <DeliveriesPage /> },
          { path: "adjustments", element: <AdjustmentsPage /> },
        ],
      },
      {
        path: "invoicing",
        element: <InvoicingLayout />,
        children: [
          { path: "ar", element: <InvoicesPage side="ar" /> },
          { path: "ap", element: <InvoicesPage side="ap" /> },
          { path: "payments", element: <PaymentsPage /> },
        ],
      },
      {
        path: "accounting",
        element: <AccountingLayout />,
        children: [
          { path: "accounts", element: <AccountsPage /> },
          { path: "journal", element: <JournalPage /> },
          { path: "trial-balance", element: <TrialBalancePage /> },
        ],
      },
      { path: "reports", element: <ReportsPage /> },
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
