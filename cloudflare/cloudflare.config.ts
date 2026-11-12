import { bindings, defineConfig, exports } from "cf/config";

export default defineConfig(({ mode }) => {
  if (mode !== "staging") {
    throw new Error("The feasibility project only supports --mode staging.");
  }
  return {
    accountId: "4287bb6da0c60bbcc12c01a34718fb86",
    worker: {
      name: "erp-feasibility",
      entrypoint: "./src/entry.py",
      compatibilityDate: "2026-11-12",
      compatibilityFlags: ["python_workers"],
      observability: { enabled: true, traces: { enabled: true } },
      exports: { OrganizationCommands: exports.durableObject({ storage: "sqlite" }) },
      env: {
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
      },
    },
  };
});
