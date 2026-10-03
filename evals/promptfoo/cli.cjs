// Stable Windows/Unix environment for the pinned upstream CLI.
const { spawnSync } = require("node:child_process");
const path = require("node:path");
const run = spawnSync(process.execPath, [
  path.join(__dirname, "node_modules/promptfoo/dist/src/entrypoint.js"),
  ...process.argv.slice(2),
], {
  stdio: "inherit",
  env: {
    ...process.env,
    PROMPTFOO_DISABLE_TELEMETRY: process.env.PROMPTFOO_DISABLE_TELEMETRY ?? "1",
    PYTHONUTF8: "1",
    PYTHONIOENCODING: "utf-8",
  },
});
if (run.error) console.error(run.error.message);
process.exit(run.status ?? 1);
