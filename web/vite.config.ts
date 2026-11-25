import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  // GitHub Pages serves the project at /<repo>/, so the hosted build needs a base path.
  // Everything else — dev, Docker, nginx — stays at the root.
  base: process.env.BASE_PATH ?? "/",
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      // Everything the console calls lives on the API. Proxying keeps the browser
      // on one origin in development.
      "/api": {
        target: process.env.ANUMATI_API ?? "http://localhost:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
  build: { outDir: "dist", sourcemap: false },
});
