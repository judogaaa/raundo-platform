import { resolve } from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const apiTarget = process.env.VITE_API_TARGET || "http://127.0.0.1:8090";

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      input: {
        main: resolve(__dirname, "index.html"),
        checkout: resolve(__dirname, "checkout.html"),
        landing: resolve(__dirname, "landing.html"),
      },
    },
  },
  server: {
    host: "127.0.0.1",
    port: 3000,
    proxy: {
      // Public checkout API lives under /api/public on the API process too.
      "/api/public": { target: apiTarget },
      "/legal": { target: apiTarget },
      "/api": { target: apiTarget, rewrite: (p) => p.replace(/^\/api/, "") },
    },
  },
});
