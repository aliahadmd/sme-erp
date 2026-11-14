async (page, credentials, fixture) => {
  const origin = "https://erp-migration-staging-20261113.aliahad.workers.dev";
  if (new URL(page.url()).origin !== origin)
    throw new Error("Unexpected financial proof origin");
  let signedIn = false;
  let installed = false;
  let result;
  try {
    await page
      .getByRole("textbox", { name: "Email", exact: true })
      .fill(credentials.email);
    await page
      .getByRole("textbox", { name: "Password", exact: true })
      .fill(credentials.password);
    const ready = page.waitForResponse(
      (r) => new URL(r.url()).pathname === "/api/auth/me" && r.status() === 200,
    );
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    await ready;
    signedIn = true;
    await page
      .getByRole("complementary")
      .getByRole("link", { name: "Invoicing", exact: true })
      .click();
    await page
      .getByRole("main")
      .locator("nav")
      .getByRole("link", { name: "Payments", exact: true })
      .click();
    await page
      .getByRole("heading", { name: "Payments", exact: true })
      .waitFor();
    await page
      .getByRole("main")
      .getByRole("button", { name: "Record payment", exact: true })
      .click();
    let dialog = page.getByRole("dialog");
    await dialog.getByRole("combobox").nth(2).click();
    await page
      .getByRole("option", { name: fixture.party_name, exact: true })
      .click();
    await dialog.getByLabel("Amount *", { exact: true }).fill("12.34");
    const initial = dialog.getByRole("button", {
      name: "Record payment",
      exact: true,
    });
    await initial.waitFor();
    await page.waitForLoadState("networkidle");
    await page.evaluate(
      ({ paymentKey, restart }) => {
        const originalFetch = window.fetch;
        const originalUUID = crypto.randomUUID;
        const probe = {
          originalFetch,
          originalUUID,
          keys: [],
          bodies: [],
          committed: [],
        };
        window.__erpPaymentRetry = probe;
        // Pin only the first user action's UUID for resumable receipt lookup.
        // The browser's real header is still checked on every transmitted retry.
        crypto.randomUUID = () => {
          crypto.randomUUID = originalUUID;
          return paymentKey;
        };
        window.fetch = async (...args) => {
          const [input, options] = args;
          const url = new URL(
            typeof input === "string" ? input : input.url,
            window.location.origin,
          );
          if (
            url.pathname !== "/api/invoicing/payments" ||
            options?.method !== "POST"
          )
            return originalFetch(...args);
          const key = new Headers(options.headers).get("Idempotency-Key");
          probe.keys.push(key);
          probe.bodies.push(options.body);
          if (
            key !== paymentKey ||
            (probe.bodies.length > 1 && options.body !== probe.bodies[0])
          ) {
            throw new Error(
              "Retry changed the submitted key or payment details",
            );
          }
          const response = await originalFetch(...args);
          if (!response.ok) return response;
          probe.committed.push(await response.clone().json());
          if (probe.committed.length === 1 && restart) {
            const headers = {
              Authorization: new Headers(options.headers).get("Authorization"),
            };
            const inspect = async () => {
              const result = await originalFetch(
                "/internal/proofs/coordinator/instance",
                { headers },
              );
              if (result.status !== 200)
                throw new Error(
                  "Coordinator instance probe failed: " + result.status,
                );
              return (await result.json()).instance_id;
            };
            const before = await inspect();
            const reset = await originalFetch(
              "/internal/proofs/coordinator/restart",
              { method: "POST", headers },
            );
            if (reset.status !== 202)
              throw new Error(
                "Coordinator reset request failed: " + reset.status,
              );
            const after = await inspect();
            probe.restart = { before, after, changed: before !== after };
            if (!probe.restart.changed)
              throw new Error(
                "Coordinator instance did not change after reset",
              );
          }
          if (probe.committed.length === 1)
            throw new TypeError("Injected response loss after commit");
          return response;
        };
      },
      { paymentKey: fixture.payment_key, restart: !!fixture.restart },
    );
    installed = true;
    await initial.click();
    await dialog
      .getByText("Injected response loss after commit", { exact: true })
      .waitFor();
    const locked = await dialog
      .getByLabel("Amount *", { exact: true })
      .isDisabled();
    const hasRetry =
      (await dialog
        .getByRole("button", { name: "Retry submitted payment", exact: true })
        .count()) === 1;
    if (hasRetry) {
      await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
      await dialog.waitFor({ state: "hidden" });
      await page
        .getByRole("main")
        .getByRole("button", { name: "Record payment", exact: true })
        .click();
      dialog = page.getByRole("dialog");
    }
    const retained = await dialog
      .getByLabel("Amount *", { exact: true })
      .inputValue();
    const retry = dialog.getByRole("button", {
      name: "Retry submitted payment",
      exact: true,
    });
    await (
      hasRetry
        ? retry
        : dialog.getByRole("button", { name: "Record payment", exact: true })
    ).click();
    if (hasRetry) await dialog.waitFor({ state: "hidden" });
    else
      await dialog
        .getByText("Retry changed the submitted key or payment details", {
          exact: true,
        })
        .waitFor();
    const proof = await page.evaluate(() => {
      const p = window.__erpPaymentRetry;
      return {
        attempts: p.keys.length,
        sameKey: p.keys.length === 2 && p.keys[0] === p.keys[1],
        sameBody: p.bodies.length === 2 && p.bodies[0] === p.bodies[1],
        payments: p.committed.map((row) => ({
          id: row.id,
          number: row.number,
          amount: row.amount,
        })),
        restart: p.restart ?? null,
      };
    });
    result = {
      ...proof,
      locked,
      retained,
      hasRetry,
      passed:
        proof.sameKey &&
        proof.sameBody &&
        locked &&
        retained === "12.34" &&
        hasRetry &&
        (!fixture.restart || proof.restart?.changed) &&
        proof.payments.length === 2 &&
        proof.payments[0].id === proof.payments[1].id,
    };
  } catch (error) {
    result = { passed: false, error: String(error).slice(0, 800) };
  } finally {
    if (installed)
      await page.evaluate(() => {
        window.fetch = window.__erpPaymentRetry.originalFetch;
        crypto.randomUUID = window.__erpPaymentRetry.originalUUID;
        delete window.__erpPaymentRetry;
      });
    if (await page.getByRole("dialog").count()) {
      await page.keyboard.press("Escape");
      await page.getByRole("dialog").waitFor({ state: "hidden" });
    }
    if (signedIn) {
      await page
        .getByRole("banner")
        .getByRole("button")
        .filter({ hasText: credentials.full_name })
        .click();
      const done = page.waitForResponse(
        (r) => new URL(r.url()).pathname === "/api/auth/logout",
      );
      await page
        .getByRole("menuitem", { name: "Sign out", exact: true })
        .click();
      if ((await done).status() !== 200)
        throw new Error("Financial proof logout failed");
      await page.waitForURL("**/login");
    }
  }
  return {
    ...result,
    assets: await page
      .locator("script[src]")
      .evaluateAll((scripts) =>
        scripts.map((script) => new URL(script.src).pathname),
      ),
  };
};
