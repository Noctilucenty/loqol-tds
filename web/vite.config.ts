import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  // Built straight into the FastAPI app so the whole thing deploys as one service.
  build: { outDir: "../app/static", emptyOutDir: true },
  server: {
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
});
