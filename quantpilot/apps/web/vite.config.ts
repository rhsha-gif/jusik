/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "node:path";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          const normalized = id.replaceAll("\\", "/");
          if (!normalized.includes("/node_modules/")) return undefined;
          if (
            normalized.includes("/react/") ||
            normalized.includes("/react-dom/") ||
            normalized.includes("/react-router/") ||
            normalized.includes("/react-router-dom/") ||
            normalized.includes("/@remix-run/router/") ||
            normalized.includes("/scheduler/") ||
            normalized.includes("/use-sync-external-store/") ||
            normalized.includes("/@tanstack/react-query/")
          ) {
            return "vendor-react";
          }
          if (normalized.includes("/recharts/") || normalized.includes("/d3-")) {
            return "vendor-charts";
          }
          if (
            normalized.includes("/lucide-react/") ||
            normalized.includes("/@radix-ui/") ||
            normalized.includes("/framer-motion/")
          ) {
            return "vendor-ui";
          }
          return undefined;
        },
      },
    },
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "src"),
    },
  },
  server: {
    port: 5173,
    strictPort: true,
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["src/test/setup.ts"],
  },
});
