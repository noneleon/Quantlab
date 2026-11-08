import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Built UI is served same-origin by `quantlab serve` (DEC-0018). In development, `npm run dev` proxies /api to a
// running `quantlab serve` on 127.0.0.1:8765.
export default defineConfig({
  plugins: [react()],
  base: "/",
  build: { outDir: "dist", sourcemap: false, assetsInlineLimit: 0 },
  server: { host: "127.0.0.1", proxy: { "/api": "http://127.0.0.1:8765" } },
  test: { environment: "node", include: ["src/**/*.test.{ts,tsx}"] },
});
