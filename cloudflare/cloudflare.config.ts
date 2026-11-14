import { bindings, defineConfig, exports, triggers } from "cf/config";

export default defineConfig(({ mode }) => {
  if (!["staging", "app-staging", "demo-staging"].includes(mode)) {
    throw new Error("Select staging (proof), app-staging (verification), or demo-staging.");
  }
  const proof = mode === "staging";
  const demo = mode === "demo-staging";
  const initializing = demo && process.env.ERP_DEMO_BOOTSTRAP === "true";
  const restartProof = mode === "app-staging" && process.env.ERP_RESTART_PROOF === "true";
  const application = demo ? "erp-demo-staging-20261114" : "erp-migration-staging-20261113";
  const eventQueue = demo ? "erp-demo-staging-events-20261114" : "erp-staging-events-20261113";
  const deadQueue = demo ? "erp-demo-staging-events-dlq-20261114" : "erp-staging-events-dlq-20261113";
  const maxRetries = 3;
  return {
    accountId: "4287bb6da0c60bbcc12c01a34718fb86",
    worker: {
      name: proof ? "erp-feasibility" : application,
      entrypoint: proof ? "./src/entry.py" : "./src/application.py",
      compatibilityDate: "2026-11-12",
      compatibilityFlags: ["python_workers"],
      observability: { enabled: true, traces: { enabled: true } },
      assets: proof ? undefined : {
        notFoundHandling: "single-page-application",
        runWorkerFirst: [
          "/api", "/api/*", "/internal", "/internal/*",
          "/healthz", "/readyz", "/openapi.json", "/redoc",
        ],
      },
      triggers: proof ? [] : [
        triggers.queue({ name: eventQueue, deadLetterQueue: deadQueue,
          maxBatchSize: 10, maxBatchTimeout: 1, maxConcurrency: 2, maxRetries }),
        triggers.queue({ name: deadQueue, maxBatchSize: 10, maxBatchTimeout: 1,
          maxConcurrency: 1, maxRetries }),
        triggers.scheduled({ schedule: "* * * * *" }),
      ],
      exports: proof
        ? { OrganizationCommands: exports.durableObject({ storage: "sqlite" }) }
        : {
          LoginGuard: exports.durableObject({ storage: "sqlite" }),
          ERPCommands: exports.durableObject({ storage: "sqlite" }),
          AIBudget: exports.durableObject({ storage: "sqlite" }),
          ERPBackups: exports.durableObject({ storage: "sqlite" }),
        },
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
          name: demo ? "erp-demo-staging-20261114" : "erp-staging-20261113",
          id: demo ? "71ee20e1-b6de-4f2a-8f8f-4a5ecd070225" : "0f278204-3190-49e1-96d2-a9312fa57e3d",
        }),
        AUTH_LIMITERS: bindings.durableObject({
          worker: application,
          exportName: "LoginGuard",
        }),
        COMMANDS: bindings.durableObject({
          worker: application,
          exportName: "ERPCommands",
        }),
        ENVIRONMENT: bindings.text("staging"),
        PUBLIC_ORIGIN: bindings.text(`https://${application}.aliahad.workers.dev`),
        ORG_ID: bindings.text(demo ? "01a1d6bf-eca3-7654-b225-ae37c723ed96" : "019a78e1-6d80-7000-8000-000000000001"),
        BOOTSTRAP_ENABLED: bindings.text(demo ? String(initializing) : "true"),
        RESTART_PROOFS_ENABLED: bindings.text(String(restartProof)),
        EVENTS: bindings.queue({ name: eventQueue }),
        JOBS_ENABLED: bindings.text(String(!initializing)),
        EVENT_QUEUE_NAME: bindings.text(eventQueue),
        DEAD_LETTER_QUEUE_NAME: bindings.text(deadQueue),
        QUEUE_MAX_RETRIES: bindings.text(String(maxRetries)),
        EMAIL: bindings.sendEmail({ allowedSenderAddresses: ["erp@aliahad.com"] }),
        EMAIL_FROM: bindings.text("erp@aliahad.com"),
        EMAIL_MODE: bindings.text("test"),
        AUDIT_RETENTION_DAYS: bindings.text("365"),
        BUSINESS_UTC_OFFSET_MINUTES: bindings.text("480"),
        AI: bindings.ai(),
        AI_BUDGETS: bindings.durableObject({
          worker: application, exportName: "AIBudget",
        }),
        AI_ENABLED: bindings.text("true"),
        AI_MODEL: bindings.text("@cf/meta/llama-3.3-70b-instruct-fp8-fast"),
        AI_DAILY_REQUEST_LIMIT: bindings.text("200"),
        BACKUPS: bindings.r2({ name: demo ? "erp-demo-staging-backups-20261114" : "erp-staging-backups-20261113" }),
        BACKUP_CONTROL: bindings.durableObject({
          worker: application, exportName: "ERPBackups",
        }),
        ...(!demo ? {
          RESTORE_DB: bindings.d1({
            name: "erp-recovery-staging-20261113", id: "d7bb688c-8009-4eb0-a21f-993310c23fcc",
          }),
          RESTORE_DATABASE_ID: bindings.text("d7bb688c-8009-4eb0-a21f-993310c23fcc"),
        } : {}),
        BACKUPS_ENABLED: bindings.text("true"),
        RESTORE_ENABLED: bindings.text(String(!demo)),
        BACKUP_SCHEDULE_ENABLED: bindings.text(String(!initializing)),
        BACKUP_HOUR_LOCAL: bindings.text("2"),
        BACKUP_RETENTION_DAYS: bindings.text("35"),
        BACKUP_MAX_PAUSE_SECONDS: bindings.text("600"),
      },
    },
  };
});
