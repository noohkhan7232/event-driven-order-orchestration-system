import { defineConfig, globalIgnores } from "eslint/config";
import js from "@eslint/js";
import tseslint from "typescript-eslint";
import nextPlugin from "@next/eslint-plugin-next";
import hooks from "eslint-plugin-react-hooks";
import globals from "globals";
import prettier from "eslint-config-prettier/flat";
export default defineConfig([
  globalIgnores([
    ".next/**",
    "src/lib/api/generated.ts",
    "playwright-report/**",
    "test-results/**",
    "next-env.d.ts",
  ]),
  { languageOptions: { globals: { ...globals.browser, ...globals.node } } },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["**/*.{ts,tsx}"],
    plugins: { "@next/next": nextPlugin, "react-hooks": hooks },
    rules: {
      ...nextPlugin.configs.recommended.rules,
      ...nextPlugin.configs["core-web-vitals"].rules,
      ...hooks.configs.recommended.rules,
    },
  },
  prettier,
]);
