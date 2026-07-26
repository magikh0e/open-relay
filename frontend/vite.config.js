import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// A unique id per build. Baked into the app (__BUILD_ID__) and emitted as
// version.json so a running tab can detect when a newer build is deployed.
const BUILD_ID = String(Date.now());

export default defineConfig({
  plugins: [
    react(),
    {
      name: "emit-version",
      generateBundle() {
        this.emitFile({
          type: "asset",
          fileName: "version.json",
          source: JSON.stringify({ build: BUILD_ID }),
        });
      },
    },
  ],
  define: { __BUILD_ID__: JSON.stringify(BUILD_ID) },
  // Vitest reads this same config, so tests build the app exactly as the app
  // builds, including the __BUILD_ID__ define above. Without that, any module
  // touching it fails to parse under test.
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.js"],
    include: ["src/**/*.test.{js,jsx}"],
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ""),
      },
      "/ws": {
        target: "ws://localhost:8000",
        ws: true,
      },
    },
  },
});
