import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const BACKEND = process.env.VITE_BACKEND_URL || "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    allowedHosts: ["noheartnocart.biz", ".noheartnocart.biz"],
    proxy: {
      "/api": BACKEND,
      "/secret": BACKEND,
    },
  },
});