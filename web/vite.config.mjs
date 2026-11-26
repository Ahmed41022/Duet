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
    rollupOptions: {
      input: {
        index: fileURLToPath(new URL("./index.html", import.meta.url)),
        sw: fileURLToPath(new URL("./src/sw.ts", import.meta.url)),
      },
      output: {
        entryFileNames: ({ name }) =>
          name === "sw" ? "sw.js" : "assets/[name]-[hash].js",
      },
    },
  },
});
