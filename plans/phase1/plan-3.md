# Plan 3 — Frontend Foundation (Vite + React + shadcn)

- **Depends on:** plan-1; real login wiring lands in plan-4
- **Goal:** the `web/` SPA scaffold: routing, shadcn design system, server-state
  management, typed API client generated from FastAPI's OpenAPI schema, app shell,
  and the login page ready to be wired.

## 1. Scaffold

- [ ] `pnpm create vite web --template react-ts` + `pnpm install`.
- [ ] Tailwind + shadcn: `pnpm dlx shadcn@latest init --preset b0 --template start --pointer`
      (run as given; if CLI flags moved on, follow the shadcn CLI's own guidance — the
      `b0` preset is the requirement). Add the primitives used in phase 1:
      `button input label select dialog sheet table badge tabs form toast/sonner
      dropdown-menu avatar skeleton separator card command popover`.
- [ ] Routing: **React Router 7** data router; lazy route modules per feature.
- [ ] **TanStack Query** provider; global defaults (staleTime, retry, error toast).
- [ ] ESLint from template kept strict; add `typescript-eslint` strict-ish rules.

## 2. Typed API client (single source of truth)

- [ ] Script `pnpm gen:api` → fetch `http://localhost:8000/openapi.json` →
      `openapi-typescript` generates `src/lib/api/schema.d.ts`.
- [ ] `src/lib/api/client.ts`: fetch wrapper with baseURL `VITE_API_URL`,
      JSON handling, credentials: include (refresh cookie), typed via the generated
      schema, uniform error extraction → rejects with `{code, detail, errors}`.
- [ ] Convention: one `features/<x>/api.ts` per feature exporting typed hooks
      (`useContacts`, `useCreateContact`, …) built on TanStack Query. Query keys
      centralized in `src/lib/query-keys.ts`.

## 3. Auth shell (wiring completes in plan-4)

- [ ] Auth context: `useAuth()` exposing `{user, permissions, login, logout, isLoading}`;
      access token kept in memory only; refresh handled by httpOnly cookie set by the API.
- [ ] Login page (`/login`): react-hook-form + zod schema; calls `login()`;
      friendly error on 401. Works against the real endpoint once plan-4 lands.
- [ ] Route guard: unauthenticated users → redirect `/login`; permission-based guard
      `<RequirePermission code="sales.order.create">` for later use.
- [ ] App shell (`/app` layout): left sidebar (module nav: Dashboard, CRM, Sales,
      Purchasing, Inventory, Invoicing, Accounting, Reports, Settings — pages arrive
      with their plans; placeholder pages until then), top header with global search
      placeholder, notifications bell placeholder, user menu (profile, logout),
      dark/light toggle (shadcn theme).
- [ ] Empty dashboard page at `/app` so login lands somewhere real.

## 4. Quality gates

- [ ] vitest + @testing-library: one render test for shell + login validation test.
- [ ] `make verify` extended: web `tsc --noEmit` + `eslint` + `vitest run` + `vite build`.

## Acceptance

- [ ] `make up` → `localhost:5173` shows the shell; unauthenticated visit redirects to
      `/login`; login form validates and shows a clean error (endpoint 404 until plan-4).
- [ ] `pnpm gen:api` regenerates types from the running API without manual edits.
- [ ] `make verify` green including web checks.
