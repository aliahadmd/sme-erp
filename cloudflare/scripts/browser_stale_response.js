async (page, accounts) => {
  let actor = null;
  let held = null;
  let refreshes = 0;
  let reads = 0;
  const onRequest = (request) => {
    if (new URL(request.url()).pathname === "/api/auth/refresh") refreshes += 1;
    if (new URL(request.url()).pathname === "/api/hr/leave-requests")
      reads += 1;
  };
  async function login(account) {
    await page
      .getByRole("textbox", { name: "Email", exact: true })
      .fill(account.email);
    await page
      .getByRole("textbox", { name: "Password", exact: true })
      .fill(account.password);
    const ready = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === "/api/auth/me" &&
        response.status() === 200,
    );
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    await ready;
    actor = account;
    await page
      .getByRole("banner")
      .getByRole("button")
      .filter({ hasText: account.full_name })
      .waitFor();
  }
  async function logout() {
    await page
      .getByRole("banner")
      .getByRole("button")
      .filter({ hasText: actor.full_name })
      .click();
    const ready = page.waitForResponse(
      (response) => new URL(response.url()).pathname === "/api/auth/logout",
    );
    await page.getByRole("menuitem", { name: "Sign out", exact: true }).click();
    if ((await ready).status() !== 200)
      throw new Error("Browser logout failed");
    actor = null;
    await page.waitForURL("**/login");
  }
  const match = (url) => url.pathname === "/api/hr/leave-requests";
  let captured;
  const waiting = new Promise((resolve) => {
    captured = resolve;
  });
  let intercepted = false;
  const intercept = async (route) => {
    if (!intercepted) {
      intercepted = true;
      held = route;
      captured();
    } else await route.continue();
  };
  await login(accounts.admin);
  // Observe the browser fetch promise, rather than treating the network's
  // response event as proof that the application's continuation has run.
  await page.evaluate(() => {
    const original = window.fetch;
    let resolve;
    const probe = {
      original,
      first: true,
      done: new Promise((done) => {
        resolve = done;
      }),
    };
    window.__erpStaleProbe = probe;
    window.fetch = (...args) => {
      const input = args[0];
      const url = new URL(
        typeof input === "string" ? input : (input.url ?? input.href),
        window.location.origin,
      );
      const first = probe.first && url.pathname === "/api/hr/leave-requests";
      if (first) probe.first = false;
      return original(...args).then((response) => {
        if (first) resolve();
        return response;
      });
    };
  });
  page.on("request", onRequest);
  await page.route(match, intercept);
  try {
    await page
      .getByRole("complementary")
      .getByRole("link", { name: "HR", exact: true })
      .click();
    await page
      .getByRole("heading", { name: "Employees", exact: true })
      .waitFor();
    await page
      .getByRole("link", { name: "Leave requests", exact: true })
      .click();
    await waiting;
    await logout();
    const ownData = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === "/api/hr/leave-requests" &&
        response.status() === 200,
    );
    await login(accounts.employee);
    await ownData;
    await page
      .getByRole("main")
      .getByRole("row")
      .filter({ hasText: "pending" })
      .first()
      .waitFor();
    const before = refreshes;
    const readsBefore = reads;
    const released = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === "/api/hr/leave-requests" &&
        response.status() === 401,
    );
    await held.fulfill({
      status: 401,
      contentType: "application/json",
      body: JSON.stringify({
        error: {
          code: "unauthorized",
          detail: "Delayed previous-session response",
        },
      }),
    });
    held = null;
    await released;
    await page.evaluate(async () => {
      const probe = window.__erpStaleProbe;
      await probe.done;
      await new Promise((resolve) => setTimeout(resolve, 0));
      // A later public request provides an observable round trip after the
      // stale fetch continuation has had a chance to issue a refresh.
      const response = await probe.original.call(window, "/api/meta");
      if (!response.ok)
        throw new Error("Post-response observation probe failed");
    });
    await page.waitForLoadState("networkidle");
    if (refreshes !== before || reads !== readsBefore)
      throw new Error(
        "A previous account's delayed 401 refreshed and retried under the employee session",
      );
    const rows = (await page.getByRole("main").getByRole("row").count()) - 1;
    if (rows !== 3)
      throw new Error(
        "The employee's leave scope changed after the stale response",
      );
    return {
      isolated: true,
      staleRefreshes: refreshes - before,
      staleRetries: reads - readsBefore,
      employeeRows: rows,
    };
  } finally {
    if (held) await held.fulfill({ status: 401, body: "{}" });
    await page.unroute(match, intercept);
    page.off("request", onRequest);
    await page.evaluate(() => {
      window.fetch = window.__erpStaleProbe.original;
      delete window.__erpStaleProbe;
    });
    if (actor) await logout();
  }
};
