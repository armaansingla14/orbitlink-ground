import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The API is served by uvicorn on :8000; the dev server proxies /api to it.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { "/api": process.env.ORBITLINK_API ?? "http://localhost:8000" },
  },
});
