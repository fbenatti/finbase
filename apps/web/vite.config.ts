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
    // Atrás de um proxy com outro nome de host, informe-o em VITE_ALLOWED_HOSTS
    // (separados por vírgula); o Vite 6 recusa hosts desconhecidos.
    allowedHosts: (process.env.VITE_ALLOWED_HOSTS || "").split(",").map((host) => host.trim()).filter(Boolean),
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
