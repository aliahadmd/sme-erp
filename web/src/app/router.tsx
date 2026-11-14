import type { ReactNode } from "react"
import { createBrowserRouter, Navigate } from "react-router"

import { RequireAuth, RequirePermission } from "@/app/require-auth"
import { ErrorPage } from "@/app/error-page"
import { ForbiddenPage } from "@/app/forbidden-page"
import { LoginPage } from "@/features/auth/login-page"
import { HomePage } from "@/app/home-page"
import { SectionIndex } from "@/app/section-layout"
import {
  ACCOUNTING_TABS,
  INVENTORY_TABS,
  INVOICING_TABS,
  PRODUCT_TABS,
  SALES_TABS,
  SETTINGS_TABS,
} from "@/app/section-tabs"
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
import { LeaveRequestsPage } from "@/features/hr/hr-page"
import { HRIndex, HRLayout } from "@/features/hr/layout"
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
import {
  JournalPage,
  TrialBalancePage,
} from "@/features/accounting/journal-and-trial-page"
import { ReportsPage } from "@/features/reports/reports-page"
import { OrderEditorRoute } from "@/features/documents/order-editor-page"
import { OrdersListPage } from "@/features/documents/orders-list-page"

function allowed(code: string | string[], children: ReactNode) {
  return <RequirePermission code={code}>{children}</RequirePermission>
}

export const router = createBrowserRouter([
  { path: "/login", element: <LoginPage />, ErrorBoundary: ErrorPage },
  {
    path: "/",
    ErrorBoundary: ErrorPage,
    element: <RequireAuth />,
    children: [
      { index: true, element: <HomePage /> },
      { path: "crm", element: allowed("crm.contact.read", <ContactsPage />) },
      {
        path: "products",
        element: <ProductsLayout />,
        children: [
          {
            index: true,
            element: (
              <SectionIndex tabs={PRODUCT_TABS}>
                <ProductsPage />
              </SectionIndex>
            ),
          },
          {
            path: "ai-drafts",
            element: allowed("catalog.product.update", <AiDraftsPage />),
          },
        ],
      },
      {
        path: "sales",
        element: <SalesLayout />,
        children: [
          {
            index: true,
            element: (
              <SectionIndex tabs={SALES_TABS}>
                <OrdersListPage module="sales" />
              </SectionIndex>
            ),
          },
          { path: "orders", element: <Navigate to="/sales" replace /> },
          {
            path: "orders/new",
            element: allowed(
              "sales.order.create",
              <OrderEditorRoute module="sales" />
            ),
          },
          {
            path: "orders/:id",
            element: allowed(
              "sales.order.read",
              <OrderEditorRoute module="sales" />
            ),
          },
          {
            path: "quotations",
            element: allowed("sales.quote.read", <QuotesPage />),
          },
        ],
      },
      {
        path: "purchasing",
        element: allowed(
          "purchasing.order.read",
          <OrdersListPage module="purchasing" />
        ),
      },
      {
        path: "purchasing/new",
        element: allowed(
          "purchasing.order.create",
          <OrderEditorRoute module="purchasing" />
        ),
      },
      {
        path: "purchasing/:id",
        element: allowed(
          "purchasing.order.read",
          <OrderEditorRoute module="purchasing" />
        ),
      },
      {
        path: "inventory",
        element: <InventoryLayout />,
        children: [
          {
            index: true,
            element: (
              <SectionIndex tabs={INVENTORY_TABS}>
                <StockPage />
              </SectionIndex>
            ),
          },
          {
            path: "receipts",
            element: allowed("inventory.receipt.read", <ReceiptsPage />),
          },
          {
            path: "deliveries",
            element: allowed("inventory.delivery.read", <DeliveriesPage />),
          },
          {
            path: "adjustments",
            element: allowed("inventory.adjustment.read", <AdjustmentsPage />),
          },
        ],
      },
      {
        path: "invoicing",
        element: <InvoicingLayout />,
        children: [
          { index: true, element: <SectionIndex tabs={INVOICING_TABS} /> },
          {
            path: "ar",
            element: allowed(
              "invoicing.invoice.read",
              <InvoicesPage side="ar" />
            ),
          },
          {
            path: "ap",
            element: allowed(
              "invoicing.invoice.read",
              <InvoicesPage side="ap" />
            ),
          },
          {
            path: "payments",
            element: allowed("invoicing.payment.read", <PaymentsPage />),
          },
        ],
      },
      {
        path: "accounting",
        element: <AccountingLayout />,
        children: [
          { index: true, element: <SectionIndex tabs={ACCOUNTING_TABS} /> },
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
          { index: true, element: <HRIndex /> },
          {
            path: "leave",
            element: allowed("hr.leave.request", <LeaveRequestsPage />),
          },
        ],
      },
      {
        path: "settings",
        element: <SettingsLayout />,
        children: [
          {
            index: true,
            element: (
              <SectionIndex tabs={SETTINGS_TABS}>
                <SettingsUsersPage />
              </SectionIndex>
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
              <RequirePermission
                code={[
                  "catalog.tax.read",
                  "catalog.uom.read",
                  "catalog.category.read",
                ]}
              >
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
