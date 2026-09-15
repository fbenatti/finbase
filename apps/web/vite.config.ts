import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig({
  plugins: [react()],
  build: {
    target: "es2022",
  },
  optimizeDeps: {
    esbuildOptions: {
      target: "es2022",
    },
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    // Nginx terminates TLS and forwards the private VM hostnames to the Vite
    // development server.  Vite 6 rejects unknown Host headers by default.
    allowedHosts: [
      "finopsintelligence.telefonicati.onmicrosoft.com",
      "api.finopsintelligence.telefonicati.onmicrosoft.com",
      "finopsintelligence.telefonicabigdata.com",
      "api.finopsintelligence.telefonicabigdata.com",
    ],
    proxy: {
      "/api": {
        target: process.env.VITE_API_PROXY_TARGET || "http://localhost:8001",
        changeOrigin: true,
        timeout: 180000,
        proxyTimeout: 180000,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
