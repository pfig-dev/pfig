/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "path";
import { thirdPartyNotices } from "./thirdPartyNotices";

export default defineConfig({
  plugins: [react(), thirdPartyNotices()],
  root: resolve(__dirname),
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
  build: {
    outDir: resolve(__dirname, "../pfig/vis/assets"),
    emptyOutDir: true,
    // React + React Flow + elkjs exceed Rollup's default 500 kB hint. The VS Code
    // webview CSP only nonces the entry script; splitting would load chunks without
    // a nonce and break the panel, so we keep one bundle and relax the warning.
    chunkSizeWarningLimit: 2048,
    rollupOptions: {
      input: resolve(__dirname, "index.html"),
      output: {
        entryFileNames: "assets/graph.js",
        chunkFileNames: "assets/[name].js",
        assetFileNames: "assets/[name][extname]",
      },
    },
  },
});
