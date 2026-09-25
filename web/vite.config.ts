import path from "node:path"

import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vitest/config"

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    // Docker bind mounts need polling for reliable HMR on macOS/Windows hosts.
    watch: process.env.CHOKIDAR_USEPOLLING ? { usePolling: true, interval: 300 } : undefined,
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
  },
})
