/** Shared local/CI/Workers Builds gate; no deployment or remote mutations. */
import { execFileSync } from "node:child_process";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "..");
const web = path.join(root, "..", "web");
const mode = process.env.CF_MODE ?? "demo-staging";
if (!["app-staging", "demo-staging"].includes(mode)) {
  throw new Error("Quality builds require an application staging mode");
}
if (process.env.ERP_DEMO_BOOTSTRAP === "true") {
  throw new Error("Quality builds require the demo bootstrap to be closed");
}
function run(command, args, cwd = root, env = process.env) {
  execFileSync(command, args, { cwd, env, stdio: "inherit" });
}
run("uv", ["sync", "--frozen", "--python", "3.14"]);
run("pnpm", ["install", "--frozen-lockfile"], web);
run("uv", ["run", "ruff", "check", "src", "tests", "scripts"]);
run("uv", ["run", "ruff", "format", "--check", "src", "tests", "scripts"]);
run("uv", ["run", "pytest", "-q"]);
run("uv", ["run", "python", "scripts/generate_business_schema.py", "--check"]);
run("pnpm", ["lint"], web);
run("pnpm", ["test"], web);
run("pnpm", ["vendor"]);
run("node", ["scripts/build.mjs"], root, { ...process.env, CF_MODE: mode });
console.log(`Quality gates passed; built ${mode}. No deployment or migration was run.`);
