/** Build native Python modules into cf's versioned Build Output format. */
import fs from "node:fs/promises";
import path from "node:path";
import * as configApi from "@cloudflare/config";
import * as outputApi from "@cloudflare/build-output-utils";

const root = path.resolve(import.meta.dirname, "..");
const mode = process.env.CF_MODE ?? "staging";
const context = { isPreview: false, mode };
const loaded = await configApi.loadAndParseConfig(path.join(root, "cloudflare.config.ts"), context);
if (!loaded.result.success) throw new Error(JSON.stringify(loaded.result.error));
const configuration = loaded.result.data;
await outputApi.cleanBuildOutputDir(root);
const bundle = outputApi.getWorkerBundleDir(root);
await fs.mkdir(bundle, { recursive: true });
const modules = {};
for (const item of await fs.readdir(path.join(root, "src"), { recursive: true })) {
  if (!item.endsWith(".py") && !item.endsWith(".json")) continue;
  const target = path.join(bundle, item);
  await fs.mkdir(path.dirname(target), { recursive: true });
  await fs.copyFile(path.join(root, "src", item), target);
  modules[item] = { type: item.endsWith(".py") ? "python" : "data" };
}
// Match Cloudflare's Python packaging convention. Vendored Python and WASM
// extension files are data modules; SDK JavaScript helpers remain ES modules.
const vendor = path.join(root, "python_modules");
for (const item of await fs.readdir(vendor, { recursive: true })) {
  if (item.includes("__pycache__") || item.endsWith(".pyc")) continue;
  const source = path.join(vendor, item);
  if (!(await fs.stat(source)).isFile()) continue;
  const name = `python_modules/${item}`;
  const target = path.join(bundle, name);
  await fs.mkdir(path.dirname(target), { recursive: true });
  await fs.copyFile(source, target);
  modules[name] = { type: item.startsWith("workers/") && /\.(?:mjs|js)$/.test(item) ? "esm" : "data" };
}
await outputApi.writeRootConfig(root, { accountId: configuration.accountId }, context);
await outputApi.writeWorkerConfig({
  root,
  config: configuration.worker,
  manifest: { type: "complete", mainModule: "entry.py", modules },
});
await outputApi.readBuildOutput(root);
console.log(`Built ${configuration.worker.name} (${mode}) with ${Object.keys(modules).length} modules`);
