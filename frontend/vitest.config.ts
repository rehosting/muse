import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    css: false,
    // Required for @testing-library/react's automatic afterEach(cleanup) —
    // without it, mounted trees leak across tests in the same file.
    globals: true,
  },
});
