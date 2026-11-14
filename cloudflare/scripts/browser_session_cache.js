async (page, accounts) => {
  async function login(account) {
    await page
      .getByRole("textbox", { name: "Email", exact: true })
      .fill(account.email);
    await page
      .getByRole("textbox", { name: "Password", exact: true })
      .fill(account.password);
    const ready = page.waitForResponse(
      (r) => new URL(r.url()).pathname === "/api/auth/me" && r.status() === 200,
      { timeout: 60000 },
    );
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    await ready;
  }
  async function logout(account) {
    await page
      .getByRole("banner")
      .getByRole("button")
      .filter({ hasText: account.full_name })
      .click();
    const done = page.waitForResponse(
      (r) => new URL(r.url()).pathname === "/api/auth/logout",
      { timeout: 60000 },
    );
    await page.getByRole("menuitem", { name: "Sign out", exact: true }).click();
    if ((await done).status() !== 200) throw new Error("Browser logout failed");
    await page.waitForURL("**/login");
  }
  await login(accounts.admin);
  try {
    await page
      .getByRole("complementary")
      .getByRole("link", { name: "HR", exact: true })
      .click();
    const loaded = page.waitForResponse(
      (r) =>
        new URL(r.url()).pathname === "/api/hr/leave-requests" &&
        r.status() === 200,
      { timeout: 60000 },
    );
    await page
      .getByRole("link", { name: "Leave requests", exact: true })
      .click();
    await loaded;
    await page
      .getByRole("main")
      .getByRole("row")
      .filter({ hasText: "approved" })
      .waitFor({ timeout: 10000 });
  } finally {
    await logout(accounts.admin);
  }
  // Keep the same page/application alive across accounts to exercise its cache.
  await login(accounts.employee);
  try {
    await page
      .getByRole("heading", { name: "Leave requests", exact: true })
      .waitFor({ timeout: 60000 });
    await page
      .getByRole("main")
      .getByRole("row")
      .filter({ hasText: "pending" })
      .first()
      .waitFor();
    const rows = (await page.getByRole("main").getByRole("row").count()) - 1;
    if (rows !== 3)
      throw new Error(
        `Employee displayed ${rows} leave rows after administrator logout; expected own 3`,
      );
    return { isolated: true, employeeRows: rows };
  } finally {
    await logout(accounts.employee);
  }
};
