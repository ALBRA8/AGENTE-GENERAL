import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  {
    files: ["src/server/**/*.js", "tests/**/*.js"],
    rules: {
      // These files intentionally use CommonJS because the daemon/test runner are Node scripts.
      "@typescript-eslint/no-require-imports": "off",
    },
  },
  {
    files: ["src/server/**/*.js"],
    rules: {
      // Baileys exports a function named useMultiFileAuthState; it is not a React hook.
      "react-hooks/rules-of-hooks": "off",
    },
  },
  globalIgnores([".next/**", "out/**", "build/**", "next-env.d.ts"]),
]);

export default eslintConfig;
