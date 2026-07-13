import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // Mirrors production nginx (see /etc/nginx/sites-enabled/quantx): proxy
    // /api and /ws to the backend docker container on :9000, so `npm run
    // dev` and the Playwright e2e webServer both work against real data
    // without needing a separate reverse proxy locally.
    proxy: {
      "/api": { target: "http://127.0.0.1:9000", changeOrigin: true },
      "/ws": { target: "ws://127.0.0.1:9000", ws: true },
    },
  },
})
