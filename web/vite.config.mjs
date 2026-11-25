import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";

export default defineConfig({
  base: "./",
  test: { environment: "jsdom" },
  build: {
    outDir: fileURLToPath(
      new URL("../src/duet/server/static/", import.meta.url),
    ),
    emptyOutDir: true,
  },
});
