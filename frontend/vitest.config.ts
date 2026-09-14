import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react-swc";
import path from "path";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
    // The suite mounts several large jsdom surfaces. Keep enough parallelism
    // to exercise isolation while respecting the two-core GitHub runner; four
    // workers starved timers and produced unrelated 5-second failures only in
    // the parallel gate.
    maxWorkers: 2,
    testTimeout: 15_000,
  },
  resolve: {
    alias: [
      {
        find: /^react-remove-scroll$/,
        replacement: path.resolve(__dirname, "./src/vendor/react-remove-scroll.tsx"),
      },
      {
        find: "@klm/frontend-contract-fixtures",
        replacement: path.resolve(
          __dirname,
          "./src/fixtures/dev-test/frontend-contracts.ts",
        ),
      },
      { find: "@", replacement: path.resolve(__dirname, "./src") },
    ],
  },
});
