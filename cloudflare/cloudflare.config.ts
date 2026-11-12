import { bindings, defineConfig, exports } from "cf/config";

export default defineConfig(({ mode }) => {
  if (mode !== "staging" && mode !== "app-staging") {
    throw new Error("Only --mode staging (proof) and --mode app-staging are configured.");
  }
  const proof = mode === "staging";
  return {
    accountId: "4287bb6da0c60bbcc12c01a34718fb86",
    worker: {
      name: proof ? "erp-feasibility" : "erp-migration-staging-20261113",
      entrypoint: proof ? "./src/entry.py" : "./src/application.py",
      compatibilityDate: "2026-11-12",
      compatibilityFlags: ["python_workers"],
      observability: { enabled: true, traces: { enabled: true } },
      exports: proof
        ? { OrganizationCommands: exports.durableObject({ storage: "sqlite" }) }
        : { LoginGuard: exports.durableObject({ storage: "sqlite" }) },
      env: proof ? {
        DB: bindings.d1({
          name: "erp-feasibility-20261113",
          id: "601bfd49-90f1-430e-8e09-5a2973c9134f",
        }),
        RESTORE_DB: bindings.d1({
          name: "erp-restore-proof-20261113",
          id: "052390d0-c0d7-43ca-95bc-9bb3b54a0b2f",
        }),
        BACKUPS: bindings.r2({ name: "erp-feasibility-backups-20261113" }),
        COMMANDS: bindings.durableObject({
          worker: "erp-feasibility",
          exportName: "OrganizationCommands",
        }),
        ENVIRONMENT: bindings.text("feasibility"),
      } : {
        DB: bindings.d1({
          name: "erp-staging-20261113",
          id: "0f278204-3190-49e1-96d2-a9312fa57e3d",
        }),
        AUTH_LIMITERS: bindings.durableObject({
          worker: "erp-migration-staging-20261113",
          exportName: "LoginGuard",
        }),
        ENVIRONMENT: bindings.text("staging"),
        PUBLIC_ORIGIN: bindings.text("https://erp-migration-staging-20261113.aliahad.workers.dev"),
        ORG_ID: bindings.text("019a78e1-6d80-7000-8000-000000000001"),
        BOOTSTRAP_ENABLED: bindings.text("true"),
      },
    },
  };
});
