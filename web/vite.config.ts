import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// /api/* -> expense-api, so the browser never needs CORS. Override with EXPENSE_API_URL.
const target = process.env.EXPENSE_API_URL ?? "http://localhost:8002";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      // X-Forwarded-Prefix lets expense-api's Swagger (/api/docs) find its spec behind the proxy
      "/api": { target, changeOrigin: true, rewrite: (p) => p.replace(/^\/api/, ""), headers: { "X-Forwarded-Prefix": "/api" } },
    },
  },
});
