import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: { outDir: "../src/swe_platform/static", emptyOutDir: true },
  test: { environment: "jsdom", globals: true },
});
