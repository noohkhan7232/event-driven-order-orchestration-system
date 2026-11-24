import { cp } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { spawn } from "node:child_process";
// next start cannot supply standalone assets. Keep npm start on the same entry as Docker.
await cp(
  new URL("../.next/static", import.meta.url),
  new URL("../.next/standalone/.next/static", import.meta.url),
  { recursive: true },
);
const child = spawn(
  process.execPath,
  [
    "--require",
    fileURLToPath(new URL("./ingress.cjs", import.meta.url)),
    fileURLToPath(new URL("../.next/standalone/server.js", import.meta.url)),
  ],
  {
    stdio: "inherit",
    env: { ...process.env, HOSTNAME: process.env.HOSTNAME || "127.0.0.1" },
  },
);
for (const signal of ["SIGINT", "SIGTERM"])
  process.on(signal, () => child.kill(signal));
child.on("exit", (code) => {
  process.exitCode = code || 0;
});
