import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Python packaging collects this directory as package data. It is intentionally
// relative to this frontend project so `npm run build` needs no Vite server.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../src/wt_advisor/web/static",
    // Retain previously built, potentially user-owned assets in a dirty worktree.
    emptyOutDir: false,
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
    exclude: ["e2e/**", "node_modules/**", "dist/**"],
  },
});
