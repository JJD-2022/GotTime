import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ command }) => ({
  plugins: [react()],
  base: command === "serve" ? "/" : "/static/react/",
  server: {
    host: "0.0.0.0",
    port: 5173,
    proxy: {
      "/api": "http://localhost:5000",
      "/auth/google": "http://localhost:5000"
    }
  },
  build: {
    outDir: "../static/react",
    emptyOutDir: true
  }
}));
