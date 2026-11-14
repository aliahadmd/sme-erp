async (page, accounts) => {
  const headings = {
    "/": /^Welcome back/,
    "/crm": "Contacts",
    "/products": "Products",
    "/products/ai-drafts": "AI drafts",
    "/sales": "Sales orders",
    "/sales/orders": "Sales orders",
    "/sales/quotations": "Quotations",
    "/purchasing": "Purchase orders",
    "/inventory": "Stock on hand",
    "/inventory/receipts": "Receipts",
    "/inventory/deliveries": "Deliveries",
    "/inventory/adjustments": "Adjustments",
    "/invoicing": "Customer invoices",
    "/invoicing/ar": "Customer invoices",
    "/invoicing/ap": "Supplier bills",
    "/invoicing/payments": "Payments",
    "/accounting": "Chart of accounts",
    "/accounting/accounts": "Chart of accounts",
    "/accounting/journal": "Journal entries",
    "/accounting/trial-balance": "Trial balance",
    "/reports": "Reports",
    "/hr": "Employees",
    "/hr/leave": "Leave requests",
    "/settings": "Users",
    "/settings/roles": "Roles",
    "/settings/organization": "Organization",
    "/settings/catalog": "Catalog settings",
    "/settings/currencies": "Currencies & FX rates",
    "/settings/numbering": "Document numbering",
    "/settings/audit": "Audit log",
  };
  const controls = {
    "/crm": [
      ["New contact", "crm.contact.create"],
      ["Edit", "crm.contact.update"],
      ["Archive contact", "crm.contact.archive"],
    ],
    "/products": [
      ["New product", "catalog.product.create"],
      ["Edit", "catalog.product.update"],
      ["Archive product", "catalog.product.archive"],
    ],
    "/sales": [["New sales order", "sales.order.create"]],
    "/purchasing": [["New purchase order", "purchasing.order.create"]],
    "/sales/quotations": [
      ["New quotation", "sales.quote.create"],
      ["Send", "sales.quote.update"],
      ["Accept", "sales.quote.update"],
      ["Convert", "sales.order.create"],
      ["Cancel", "sales.quote.cancel"],
    ],
    "/inventory/receipts": [
      ["New receipt", "inventory.receipt.create"],
      ["Post", "inventory.receipt.post"],
      ["Void", "inventory.receipt.void"],
    ],
    "/inventory/deliveries": [
      ["New delivery", "inventory.delivery.create"],
      ["Post", "inventory.delivery.post"],
      ["Void", "inventory.delivery.void"],
    ],
    "/inventory/adjustments": [
      ["New adjustment", "inventory.adjustment.create"],
      ["Post", "inventory.adjustment.post"],
    ],
    "/invoicing/ar": [
      ["New invoice", "invoicing.invoice.create"],
      ["Post", "invoicing.invoice.post"],
      ["Delete", "invoicing.invoice.update"],
      ["Credit note", "invoicing.invoice.create"],
      ["Email", "invoicing.invoice.post"],
      ["Void", "invoicing.invoice.void"],
    ],
    "/invoicing/ap": [
      ["New bill", "invoicing.invoice.create"],
      ["Post", "invoicing.invoice.post"],
      ["Delete", "invoicing.invoice.update"],
      ["Credit note", "invoicing.invoice.create"],
      ["Void", "invoicing.invoice.void"],
    ],
    "/invoicing/payments": [
      ["Record payment", "invoicing.payment.create"],
      ["Allocate", "invoicing.payment.create"],
      ["Void", "invoicing.payment.void"],
    ],
    "/accounting/accounts": [["New account", "accounting.account.create"]],
    "/accounting/journal": [["New manual entry", "accounting.entry.create"]],
    "/hr": [["Add employee", "hr.employee.create"]],
    "/settings": [["New user", "core.user.create"]],
    "/settings/roles": [["New role", "core.role.create"]],
  };
  const forms = new Set([
    "New contact",
    "New product",
    "New quotation",
    "New receipt",
    "New delivery",
    "New adjustment",
    "New invoice",
    "New bill",
    "Record payment",
    "New account",
    "New manual entry",
    "New user",
    "New role",
  ]);
  const roles = [];
  for (const [role, account] of Object.entries(accounts)) {
    await page.goto(new URL("/login?walkthrough=" + role, page.url()).href);
    await page.waitForLoadState("networkidle");
    await page
      .getByRole("textbox", { name: "Email", exact: true })
      .fill(account.email);
    await page
      .getByRole("textbox", { name: "Password", exact: true })
      .fill(account.password);
    const result = { role, pages: [], forms: [], failures: [] };
    const responses = (response) => {
      const path = new URL(response.url()).pathname;
      if (
        path.startsWith("/api/") &&
        path !== "/api/auth/refresh" &&
        response.status() >= 400
      ) {
        result.failures.push({
          page: new URL(page.url()).pathname,
          path,
          status: response.status(),
        });
      }
    };
    page.on("response", responses);
    const pageError = (error) =>
      result.failures.push({
        page: new URL(page.url()).pathname,
        problem: error.message.slice(0, 300),
      });
    page.on("pageerror", pageError);
    const authenticated = page.waitForResponse(
      (r) => new URL(r.url()).pathname === "/api/auth/me" && r.status() === 200,
      { timeout: 60000 },
    );
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    const identity = await (await authenticated).json();
    const can = (code) =>
      identity.is_superuser || identity.permissions.includes(code);
    await page
      .getByRole("banner")
      .getByRole("button")
      .filter({ hasText: account.full_name })
      .waitFor();
    async function inspect(label, destination) {
      // The URL changes before React commits the destination. Wait for that
      // page's heading before enumerating its tabs or observing its requests.
      const arrived = await page
        .waitForURL((url) => url.pathname === destination, { timeout: 5000 })
        .then(() => true)
        .catch(() => false);
      const expected = headings[destination];
      const renderedExpected = await page
        .getByRole("main")
        .getByRole("heading", {
          level: 1,
          name: expected,
          exact: typeof expected === "string",
        })
        .waitFor({ timeout: 3000 })
        .then(() => true)
        .catch(() => false);
      await page.waitForLoadState("networkidle");
      const rendered = await page
        .getByRole("main")
        .getByRole("heading", { level: 1 })
        .allTextContents();
      result.pages.push({
        label,
        path: new URL(page.url()).pathname,
        headings: rendered,
      });
      if (!arrived || !renderedExpected) {
        result.failures.push({
          page: new URL(page.url()).pathname,
          problem: "Expected " + destination + " with heading " + expected,
        });
      }
      if (!arrived || !renderedExpected) return false;
      for (const [name, permission] of controls[destination] ?? []) {
        const button = page
          .getByRole("main")
          .getByRole("button", { name, exact: true });
        const count = await button.count();
        if (!can(permission) && count)
          result.failures.push({
            page: destination,
            problem: "Unauthorized action visible: " + name,
          });
        const primary = forms.has(name) || name.startsWith("New ");
        if (primary && can(permission) && !count)
          result.failures.push({
            page: destination,
            problem: "Permitted action missing: " + name,
          });
        if (forms.has(name) && can(permission) && count) {
          await button.click();
          const dialog = page.getByRole("dialog");
          await dialog.waitFor();
          await page.waitForLoadState("networkidle");
          result.forms.push({ page: destination, name });
          await dialog
            .getByRole("button", { name: "Cancel", exact: true })
            .click();
          await dialog.waitFor({ state: "hidden" });
        }
        if (name === "New sales order" || name === "New purchase order") {
          if (!can(permission) || !count) continue;
          await button.click();
          await page.getByRole("heading", { name, exact: true }).waitFor();
          await page.waitForLoadState("networkidle");
          result.forms.push({ page: new URL(page.url()).pathname, name });
          await page
            .getByRole("button", { name: "Back to list", exact: true })
            .click();
          await page.waitForURL((url) => url.pathname === destination);
          await page
            .getByRole("main")
            .getByRole("heading", { level: 1, name: expected, exact: true })
            .waitFor();
        }
      }
      return true;
    }
    try {
      const homes = {
        warehouse: "/inventory",
        hr: "/hr",
        employee: "/hr/leave",
        employee_peer: "/hr/leave",
      };
      await inspect("Sign-in landing", homes[role] ?? "/");
      const sections = await page
        .getByRole("complementary")
        .getByRole("link")
        .evaluateAll((links) =>
          links.map((link) => ({
            label: link.textContent.trim(),
            href: link.getAttribute("href"),
          })),
        );
      for (const section of sections) {
        await page
          .getByRole("complementary")
          .getByRole("link", { name: section.label, exact: true })
          .click();
        const destination =
          section.href === "/invoicing"
            ? "/invoicing/ar"
            : section.href === "/accounting"
              ? "/accounting/accounts"
              : section.href === "/inventory" && role === "purchasing"
                ? "/inventory/receipts"
                : section.href === "/settings" && role === "viewer"
                  ? "/settings/catalog"
                  : section.href === "/hr" && role.startsWith("employee")
                    ? "/hr/leave"
                    : section.href;
        await inspect(section.label, destination);
        const tabs = await page
          .getByRole("main")
          .locator("nav")
          .getByRole("link")
          .evaluateAll((links) =>
            links.map((link) => ({
              label: link.textContent.trim(),
              href: link.getAttribute("href"),
            })),
          );
        for (const tab of tabs) {
          if (new URL(page.url()).pathname === tab.href) continue;
          await page
            .getByRole("main")
            .locator("nav")
            .getByRole("link", { name: tab.label, exact: true })
            .click();
          const arrived = await inspect(
            section.label + ": " + tab.label,
            tab.href === "/sales/orders" ? "/sales" : tab.href,
          );
          if (!arrived) break;
        }
      }
      const forbidden = {
        accountant: "/settings/roles",
        sales: "/accounting/accounts",
        purchasing: "/invoicing/ar",
        warehouse: "/settings/roles",
        hr: "/invoicing/ar",
        viewer: "/settings/roles",
        employee: "/products",
        employee_peer: "/products",
      }[role];
      if (forbidden) {
        await page.goto(new URL(forbidden, page.url()).href);
        await page
          .getByRole("heading", {
            name: "You don't have permission for this area",
            exact: true,
          })
          .waitFor();
        await page.waitForLoadState("networkidle");
        result.pages.push({
          label: "Restricted direct link",
          path: forbidden,
          headings: ["Forbidden"],
        });
      }
    } catch (error) {
      result.failures.push({
        page: new URL(page.url()).pathname,
        problem: String(error).slice(0, 300),
      });
    } finally {
      page.off("response", responses);
      page.off("pageerror", pageError);
      if (await page.getByRole("dialog").count()) {
        await page.keyboard.press("Escape");
        await page.getByRole("dialog").waitFor({ state: "hidden" });
      }
      await page
        .getByRole("banner")
        .getByRole("button")
        .filter({ hasText: account.full_name })
        .click();
      const signedOut = page.waitForResponse(
        (r) => new URL(r.url()).pathname === "/api/auth/logout",
        { timeout: 60000 },
      );
      await page
        .getByRole("menuitem", { name: "Sign out", exact: true })
        .click();
      if ((await signedOut).status() !== 200)
        throw new Error("Browser logout failed for " + role);
      await page.waitForURL("**/login");
    }
    roles.push(result);
  }
  return { passed: roles.every((role) => !role.failures.length), roles };
};
