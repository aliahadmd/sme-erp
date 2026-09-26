import { createBrowserRouter, Navigate } from "react-router"

import { RequireAuth, RequirePermission } from "@/app/require-auth"
import { ErrorPage } from "@/app/error-page"
import { ForbiddenPage } from "@/app/forbidden-page"
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
import { SalesLayout } from "@/features/sales/layout"
import { QuotesPage } from "@/features/quotes/quotes-page"
import { NumberingPage } from "@/features/settings/numbering-page"
import { CurrenciesPage } from "@/features/settings/currencies-page"
import { ProductsLayout } from "@/features/products/layout"
import { AiDraftsPage } from "@/features/ai/ai-drafts-page"
import { EmployeesPage, LeaveRequestsPage } from "@/features/hr/hr-page"
import { HRLayout } from "@/features/hr/layout"
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
  { path: "/login", element: <LoginPage />, ErrorBoundary: ErrorPage },
  {
    path: "/",
    ErrorBoundary: ErrorPage,
    element: <RequireAuth />,
    children: [
      { index: true, element: <DashboardPage /> },
      { path: "crm", element: <ContactsPage /> },
      {
        path: "products",
        element: <ProductsLayout />,
        children: [
          { index: true, element: <ProductsPage /> },
          { path: "ai-drafts", element: <AiDraftsPage /> },
        ],
      },
      {
        path: "sales",
        element: <SalesLayout />,
        children: [
          { index: true, element: <OrdersListPage module="sales" /> },
          { path: "orders/new", element: <OrderEditorRoute module="sales" /> },
          { path: "orders/:id", element: <OrderEditorRoute module="sales" /> },
          { path: "quotations", element: <QuotesPage /> },
        ],
      },
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
          {
            path: "accounts",
            element: (
              <RequirePermission code="accounting.account.read">
                <AccountsPage />
              </RequirePermission>
            ),
          },
          {
            path: "journal",
            element: (
              <RequirePermission code="accounting.entry.read">
                <JournalPage />
              </RequirePermission>
            ),
          },
          {
            path: "trial-balance",
            element: (
              <RequirePermission code="accounting.entry.read">
                <TrialBalancePage />
              </RequirePermission>
            ),
          },
        ],
      },
      {
        path: "reports",
        element: (
          <RequirePermission code="reports.view">
            <ReportsPage />
          </RequirePermission>
        ),
      },
      { path: "403", element: <ForbiddenPage /> },
      {
        path: "hr",
        element: <HRLayout />,
        children: [
          { index: true, element: <EmployeesPage /> },
          { path: "leave", element: <LeaveRequestsPage /> },
        ],
      },
      {
        path: "settings",
        element: <SettingsLayout />,
        children: [
          {
            index: true,
            element: (
              <RequirePermission code="core.user.read">
                <SettingsUsersPage />
              </RequirePermission>
            ),
          },
          {
            path: "roles",
            element: (
              <RequirePermission code="core.role.read">
                <SettingsRolesPage />
              </RequirePermission>
            ),
          },
          {
            path: "organization",
            element: (
              <RequirePermission code="core.org.read">
                <SettingsOrganizationPage />
              </RequirePermission>
            ),
          },
          {
            path: "catalog",
            element: (
              <RequirePermission code="catalog.tax.read">
                <CatalogSettingsPage />
              </RequirePermission>
            ),
          },
          {
            path: "currencies",
            element: (
              <RequirePermission code="core.settings.read">
                <CurrenciesPage />
              </RequirePermission>
            ),
          },
          {
            path: "numbering",
            element: (
              <RequirePermission code="core.settings.read">
                <NumberingPage />
              </RequirePermission>
            ),
          },
          {
            path: "audit",
            element: (
              <RequirePermission code="core.audit.read">
                <SettingsAuditPage />
              </RequirePermission>
            ),
          },
        ],
      },
    ],
  },
  { path: "*", element: <Navigate to="/" replace /> },
])
