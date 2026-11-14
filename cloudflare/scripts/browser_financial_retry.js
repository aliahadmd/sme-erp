async (page, credentials, fixture) => {
  const origin = "https://erp-migration-staging-20261113.aliahad.workers.dev";
  if (new URL(page.url()).origin !== origin)
    throw new Error("Unexpected proof origin");
  await page.goto(origin + "/login?financial-retry=" + fixture.run);
  if (new URL(page.url()).origin !== origin)
    throw new Error("Financial proof navigation changed origin");
  const manual = fixture.scenario === "manual";
  let signedIn = false,
    installed = false,
    result;
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
      .getByRole("link", {
        name: manual ? "Accounting" : "Inventory",
        exact: true,
      })
      .click();
    await page
      .getByRole("main")
      .locator("nav")
      .getByRole("link", {
        name: manual ? "Journal entries" : "Adjustments",
        exact: true,
      })
      .click();
    await page
      .getByRole("heading", {
        name: manual ? "Journal entries" : "Adjustments",
        exact: true,
      })
      .waitFor();
    const newLabel = manual ? "New manual entry" : "New adjustment";
    await page.getByRole("button", { name: newLabel, exact: true }).click();
    let dialog = page.getByRole("dialog");
    if (manual) {
      await dialog.getByLabel("Memo", { exact: true }).fill(fixture.memo);
      for (let n = 0; n < 2; n++) {
        await dialog.getByRole("combobox").nth(n).click();
        await page
          .getByRole("option", {
            name: fixture.accounts[n].code + " · " + fixture.accounts[n].name,
            exact: true,
          })
          .click();
      }
      await dialog.getByLabel("Amount", { exact: true }).fill("12.34");
    } else {
      await dialog.getByRole("combobox").nth(1).click();
      await page
        .getByRole("option", {
          name: fixture.product.name + " (" + fixture.product.sku + ")",
          exact: true,
        })
        .click();
      await dialog
        .getByLabel("Quantity (negative for shrinkage)", { exact: true })
        .fill("-1.0000");
    }
    await page.evaluate(
      ({ scenario, createKey, postKey }) => {
        const originalFetch = window.fetch,
          originalUUID = crypto.randomUUID;
        const p = {
          originalFetch,
          originalUUID,
          attempts: [],
          lost: false,
          blocked: false,
        };
        window.__erpFinancialRetry = p;
        let uuids = 0;
        crypto.randomUUID = () => {
          uuids++;
          if (uuids === 1) return createKey;
          crypto.randomUUID = originalUUID;
          return postKey;
        };
        const manual = scenario === "manual";
        const base = manual
          ? "/api/accounting/journal-entries"
          : "/api/inventory/adjustments";
        window.fetch = async (...args) => {
          const [input, options] = args;
          const path = new URL(
            typeof input === "string" ? input : input.url,
            location.origin,
          ).pathname;
          if (
            options?.method !== "POST" ||
            !(
              path === base ||
              (!manual && path.startsWith(base + "/") && path.endsWith("/post"))
            )
          )
            return originalFetch(...args);
          const step = path === base ? "create" : "post";
          const key = new Headers(options.headers).get("Idempotency-Key");
          p.authorization = new Headers(options.headers).get("Authorization");
          const attempt = { step, path, key, body: options.body ?? null };
          const prior = p.attempts.find((a) => a.step === step);
          p.attempts.push(attempt);
          if (
            (step === "create" && key !== createKey) ||
            (step === "post" &&
              scenario !== "adjustment-rejected" &&
              key !== postKey) ||
            (prior && (prior.path !== path || prior.body !== attempt.body))
          ) {
            p.blocked = true;
            throw new TypeError(
              "Probe blocked a changed command before transmission",
            );
          }
          const response = await originalFetch(...args);
          attempt.status = response.status;
          const body = await response.clone().json();
          attempt.result = {
            id: body.id,
            number: body.number,
            status: body.status,
          };
          const lose =
            response.ok &&
            !p.lost &&
            (manual ||
              (scenario === "adjustment-create" && step === "create") ||
              (scenario === "adjustment-post" && step === "post"));
          if (lose) {
            p.lost = true;
            throw new TypeError("Injected committed response loss");
          }
          return response;
        };
      },
      {
        scenario: fixture.scenario,
        createKey: fixture.create_key,
        postKey: fixture.post_key,
      },
    );
    installed = true;
    const initialLabel = manual ? "Post entry" : "Create & post";
    await dialog
      .getByRole("button", { name: initialLabel, exact: true })
      .click();
    await dialog
      .getByText(
        fixture.scenario === "adjustment-rejected"
          ? /^Insufficient stock:/
          : "Something went wrong",
        { exact: true },
      )
      .waitFor();
    const inputLabel = manual ? "Amount" : "Quantity (negative for shrinkage)";
    const locked = await dialog
      .getByLabel(inputLabel, { exact: true })
      .isDisabled();
    const retryLabel = manual
      ? "Retry submitted entry"
      : "Retry submitted adjustment";
    const hasRetry =
      (await dialog
        .getByRole("button", { name: retryLabel, exact: true })
        .count()) === 1;
    if (fixture.scenario === "adjustment-rejected") {
      await page.evaluate(
        async ({ id, key }) => {
          const p = window.__erpFinancialRetry;
          const response = await p.originalFetch.call(
            window,
            `/api/inventory/receipts/${id}/post`,
            {
              method: "POST",
              credentials: "include",
              headers: {
                Authorization: p.authorization,
                "Idempotency-Key": key,
              },
            },
          );
          if (!response.ok)
            throw new Error("Could not replenish the proof fixture");
        },
        { id: fixture.receipt.id, key: fixture.receipt_post_key },
      );
    }
    if (hasRetry) {
      await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
      await dialog.waitFor({ state: "hidden" });
      await page.getByRole("button", { name: newLabel, exact: true }).click();
      dialog = page.getByRole("dialog");
    }
    const retained = await dialog
      .getByLabel(inputLabel, { exact: true })
      .inputValue();
    await dialog
      .getByRole("button", {
        name: hasRetry ? retryLabel : initialLabel,
        exact: true,
      })
      .click();
    if (hasRetry) await dialog.waitFor({ state: "hidden" });
    else await page.waitForFunction(() => window.__erpFinancialRetry.blocked);
    const proof = await page.evaluate(() => {
      const p = window.__erpFinancialRetry;
      return { attempts: p.attempts, lost: p.lost, blocked: p.blocked };
    });
    const creates = proof.attempts.filter((a) => a.step === "create");
    const posts = proof.attempts.filter((a) => a.step === "post");
    const affected =
      manual || fixture.scenario === "adjustment-create" ? creates : posts;
    const acknowledged =
      affected.length === 2 &&
      affected[1].status >= 200 &&
      affected[1].status < 300 &&
      (fixture.scenario === "adjustment-rejected"
        ? affected[0].status === 409
        : affected[0].result.id === affected[1].result.id &&
          affected[0].key === affected[1].key);
    result = {
      ...proof,
      locked,
      retained,
      hasRetry,
      passed:
        hasRetry &&
        locked &&
        !proof.blocked &&
        Number(retained) === (manual ? 12.34 : -1) &&
        acknowledged &&
        creates.length ===
          (manual || fixture.scenario === "adjustment-create" ? 2 : 1) &&
        posts.length ===
          (manual ? 0 : fixture.scenario === "adjustment-create" ? 1 : 2),
    };
  } catch (error) {
    result = { passed: false, error: String(error).slice(0, 800) };
  } finally {
    if (installed)
      await page.evaluate(() => {
        const p = window.__erpFinancialRetry;
        window.fetch = p.originalFetch;
        crypto.randomUUID = p.originalUUID;
        delete window.__erpFinancialRetry;
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
      const logout = page.waitForResponse(
        (r) => new URL(r.url()).pathname === "/api/auth/logout",
      );
      await page
        .getByRole("menuitem", { name: "Sign out", exact: true })
        .click();
      if ((await logout).status() !== 200)
        throw new Error("Proof logout failed");
      await page.waitForURL("**/login");
    }
  }
  return {
    ...result,
    assets: await page
      .locator("script[src]")
      .evaluateAll((s) => s.map((i) => new URL(i.src).pathname)),
  };
};
