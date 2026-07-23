import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Proxy /api and /ws to the FastAPI backend during development so the browser
// talks to a single origin (no CORS headaches, cookies/WS upgrade just work).
export default defineConfig({
  plugins: [react()],
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
