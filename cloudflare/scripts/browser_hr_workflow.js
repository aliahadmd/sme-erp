async (page, fixture) => {
  const origin = "https://erp-migration-staging-20261113.aliahad.workers.dev";
  let account = null;
  const checks = [], mutations = [];
  const ensure = (value, message) => { if (!value) throw new Error(message); };
  const row = (date) => page.getByRole("main").getByRole("row")
    .filter({ hasText: date }).filter({ hasText: fixture.leave_type.name });

  async function uninstall() {
    await page.evaluate(() => {
      const p = window.__erpHrProbe;
      if (!p) return;
      window.fetch = p.fetch;
      crypto.randomUUID = p.uuid;
      delete window.__erpHrProbe;
    });
  }
  async function logout() {
    await uninstall();
    if (!account) return;
    await page.getByRole("banner").getByRole("button")
      .filter({ hasText: account.full_name }).click();
    const done = page.waitForResponse(r => new URL(r.url()).pathname === "/api/auth/logout");
    await page.getByRole("menuitem", { name: "Sign out", exact: true }).click();
    ensure((await done).status() === 200, "Browser sign-out failed");
    await page.waitForURL("**/login");
    account = null;
  }
  async function login(label) {
    await logout();
    await page.goto(origin + "/login?hr-workflow=" + fixture.run);
    ensure(new URL(page.url()).origin === origin, "Unexpected login origin");
    const next = fixture.accounts[label];
    await page.getByRole("textbox", { name: "Email", exact: true }).fill(next.email);
    await page.getByRole("textbox", { name: "Password", exact: true }).fill(next.password);
    const ready = page.waitForResponse(r => new URL(r.url()).pathname === "/api/auth/me" && r.status() === 200);
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    const me = await (await ready).json();
    ensure(me.id === next.user_id, "Wrong browser actor");
    account = next;
    const loaded = page.waitForResponse(r => new URL(r.url()).pathname === "/api/hr/leave-requests" && r.status() === 200);
    await page.goto(origin + "/hr/leave");
    await loaded;
    await page.getByRole("heading", { name: "Leave requests", exact: true }).waitFor();
    await page.getByRole("combobox").nth(1).locator(`option[value="${fixture.leave_type.id}"]`).waitFor({ state: "attached" });
    await page.getByRole("combobox").nth(0).locator(`option[value="${next.employee_id}"]`).waitFor({ state: "attached" });
  }
  async function probe(key, path, body, lose = false) {
    await uninstall();
    await page.evaluate(({ key, path, body, lose }) => {
      const normalize = value => value === null ? null : JSON.stringify(Object.fromEntries(Object.entries(JSON.parse(value)).sort(([a], [b]) => a.localeCompare(b))));
      const p = { fetch: window.fetch, uuid: crypto.randomUUID, attempts: [], lost: false, blocked: false };
      window.__erpHrProbe = p;
      crypto.randomUUID = () => { crypto.randomUUID = p.uuid; return key; };
      window.fetch = async (...args) => {
        const [input, options] = args;
        const target = new URL(typeof input === "string" ? input : input.url, location.origin).pathname;
        if (options?.method !== "POST" || target !== path) return p.fetch.apply(window, args);
        const actual = { key: new Headers(options.headers).get("Idempotency-Key"), body: options.body ?? null };
        p.attempts.push(actual);
        if (actual.key !== key || normalize(actual.body) !== normalize(body)) {
          p.blocked = true;
          throw new TypeError("Probe blocked changed HR input/key before transmission");
        }
        let response;
        try { response = await p.fetch.apply(window, args); }
        catch (error) { actual.error = String(error); throw error; }
        actual.status = response.status;
        const result = await response.clone().json();
        actual.result = { id: result.id, status: result.status };
        if (lose && response.ok && !p.lost) {
          p.lost = true;
          throw new TypeError("Injected committed HR response loss");
        }
        return response;
      };
    }, { key, path, body: body === undefined ? null : JSON.stringify(body), lose });
  }
  async function observed() {
    return await page.evaluate(() => {
      const p = window.__erpHrProbe;
      return { attempts: p.attempts, lost: p.lost, blocked: p.blocked };
    });
  }
  async function submit(letter, lose = false) {
    const date = fixture.dates[letter];
    const body = { employee_id: fixture.accounts.employee.employee_id, type_id: fixture.leave_type.id, date_from: date, date_to: date };
    const employee = page.getByRole("combobox").nth(0);
    ensure(await employee.isDisabled(), "Self-service employee selector must be locked");
    ensure(await employee.inputValue() === body.employee_id, "Self-service employee differs");
    await page.getByRole("combobox").nth(1).selectOption(body.type_id);
    await page.locator('input[type="date"]').nth(0).fill(date);
    await page.locator('input[type="date"]').nth(1).fill(date);
    await probe(fixture.keys["submit" + letter], "/api/hr/leave-requests", body, lose);
    await page.getByRole("button", { name: "Submit", exact: true }).click();
    await page.waitForFunction(() => {
      const p = window.__erpHrProbe;
      return p?.blocked || p?.attempts[0]?.status !== undefined || p?.attempts[0]?.error;
    });
    const first = await observed();
    ensure(!first.blocked && first.attempts[0]?.status === 201, "Submission did not acknowledge the expected command");
    if (!lose) {
      await row(date).getByText("pending", { exact: true }).waitFor();
      const result = await observed();
      mutations.push({ action: "submit" + letter, ...result });
      await uninstall();
      return result.attempts[0].result.id;
    }
  }
  async function decide(letter, action, id) {
    await probe(fixture.keys[action.toLowerCase() + letter], `/api/hr/leave-requests/${id}/${action.toLowerCase()}`, undefined);
    await row(fixture.dates[letter]).getByRole("button", { name: action, exact: true }).click();
    const status = { Approve: "approved", Reject: "rejected", Cancel: "cancelled" }[action];
    await row(fixture.dates[letter]).getByText(status, { exact: true }).waitFor();
    const result = await observed();
    ensure(result.attempts.length === 1 && result.attempts[0].status === 200, "Decision did not acknowledge one command");
    mutations.push({ action: action + letter, ...result });
    await uninstall();
  }

  let result;
  try {
    await login("employee");
    ensure(await row(fixture.dates.self).count() === 0, "Employee sees another person's request");
    checks.push("own-request scope");
    const a = await submit("A");
    await page.reload();
    await row(fixture.dates.A).getByText("pending", { exact: true }).waitFor();
    checks.push("submission survives reload");
    await login("approver");
    const own = row(fixture.dates.self);
    ensure(await own.getByRole("button", { name: "Approve", exact: true }).count() === 0, "Approver can approve own request in UI");
    ensure(await own.getByRole("button", { name: "Reject", exact: true }).count() === 0, "Approver can reject own request in UI");
    ensure(await own.getByRole("button", { name: "Cancel", exact: true }).count() === 1, "Approver cannot cancel own pending request");
    checks.push("self-approval hidden");
    await decide("A", "Approve", a);
    await login("employee");
    await row(fixture.dates.A).getByText("approved", { exact: true }).waitFor();
    const b = await submit("B");
    await login("approver");
    await decide("B", "Reject", b);
    await login("employee");
    await row(fixture.dates.B).getByText("rejected", { exact: true }).waitFor();
    const c = await submit("C");
    await decide("C", "Cancel", c);
    await page.reload();
    for (const [letter, status] of [["A", "approved"], ["B", "rejected"], ["C", "cancelled"]])
      await row(fixture.dates[letter]).getByText(status, { exact: true }).waitFor();
    checks.push("approve/reject/cancel and reload");
    await submit("D", true);
    await page.waitForFunction(() => window.__erpHrProbe?.lost);
    await page.getByText(/^(Something went wrong|Injected committed HR response loss)$/).waitFor();
    const locked = await page.getByRole("combobox").nth(1).isDisabled()
      && await page.locator('input[type="date"]').nth(0).isDisabled()
      && await page.locator('input[type="date"]').nth(1).isDisabled();
    const retry = page.getByRole("button", { name: "Retry submitted request", exact: true });
    const hasRetry = await retry.count() === 1;
    await (hasRetry ? retry : page.getByRole("button", { name: "Submit", exact: true })).click();
    await page.waitForFunction(() => {
      const p = window.__erpHrProbe;
      return p.blocked || p.attempts[1]?.status === 201;
    });
    const loss = await observed();
    mutations.push({ action: "submitD", ...loss });
    const same = loss.attempts.length === 2 && loss.attempts[0].key === loss.attempts[1].key
      && loss.attempts[0].result?.id === loss.attempts[1].result?.id && loss.attempts[1].status === 201;
    await uninstall();
    await page.reload();
    await row(fixture.dates.D).getByText("pending", { exact: true }).waitFor();
    checks.push("committed request visible after reload");
    result = { passed: locked && hasRetry && same && !loss.blocked, checks, mutations, locked, hasRetry, loss };
  } catch (error) {
    const activeProbe = await page.evaluate(() => {
      const p = window.__erpHrProbe;
      return p ? { attempts: p.attempts, lost: p.lost, blocked: p.blocked } : null;
    });
    result = { passed: false, checks, mutations, activeProbe, error: String(error).slice(0, 1000) };
  } finally {
    await logout();
  }
  return { ...result, assets: await page.locator("script[src]").evaluateAll(s => s.map(i => new URL(i.src).pathname)) };
};
