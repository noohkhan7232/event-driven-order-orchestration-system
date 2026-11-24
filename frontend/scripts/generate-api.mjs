import openapiTS, { astToString } from "openapi-typescript";
import { readFile, writeFile } from "node:fs/promises";
const target = new URL("../src/lib/api/generated.ts", import.meta.url);
const text = astToString(
  await openapiTS(new URL("../../docs/openapi.json", import.meta.url)),
);
if (process.argv.includes("--check")) {
  if ((await readFile(target, "utf8")) !== text) {
    console.error("API types are stale; run npm run gen:api");
    process.exitCode = 1;
  } else console.log("API types are fresh");
} else {
  await writeFile(target, text);
  console.log("Generated API types from docs/openapi.json");
}
