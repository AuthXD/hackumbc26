import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The backend runs on :8000. Proxying keeps the browser on one origin (no CORS, same ws host).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/ws": { target: "ws://127.0.0.1:8000", ws: true },
    },
  },
});
