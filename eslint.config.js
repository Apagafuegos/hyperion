import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["node_modules/", "src/hyperion/static/", "test-results/", "playwright-report/"] },
  tseslint.configs.recommended,
);
