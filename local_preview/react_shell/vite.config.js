import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

export default defineConfig({
  plugins: [react()],
  root: path.resolve(__dirname),
  base: "/",
  build: {
    // Preserve the Vite 5 browser floor when upgrading the toolchain.
    target: ["es2020", "edge88", "firefox78", "chrome87", "safari14"],
    outDir: path.resolve(__dirname, "../web"),
    emptyOutDir: false,
    sourcemap: false,
    rollupOptions: {
      input: path.resolve(__dirname, "index.html"),
      output: {
        entryFileNames: "assets/index.js",
        chunkFileNames: "assets/chunk-[name].js",
      },
    },
  },
});
