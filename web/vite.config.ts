import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// No API URL anywhere: the app and the API share an origin, the API under /api.
// In development the Vite server forwards /api to the local server.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://127.0.0.1:8000" } },
  build: { outDir: "dist", sourcemap: true },
  test: {
    environment: "jsdom",
    include: ["tests/unit/**/*.test.{ts,tsx}"],
    setupFiles: ["tests/unit/setup.ts"],
  },
});
