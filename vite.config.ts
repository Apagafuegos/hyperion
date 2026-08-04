import path from "node:path";
import { defineConfig } from "vite";

export default defineConfig({
  build: {
    outDir: path.resolve(__dirname, "src/hyperion/static"),
    emptyOutDir: true,
    manifest: false,
    sourcemap: false,
    rollupOptions: {
      input: {
        atlas: path.resolve(__dirname, "frontend/atlas.ts"),
        styles: path.resolve(__dirname, "frontend/atlas.css"),
      },
      output: {
        entryFileNames: "assets/[name].js",
        assetFileNames: "assets/[name][extname]",
      },
    },
  },
});
