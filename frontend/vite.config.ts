import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // Pinned to 127.0.0.1 (not the default 'localhost') so the frontend
    // shares a hostname with the backend — Spotify requires the OAuth
    // redirect to use 127.0.0.1, which anchors the session cookie there.
    // Browsers treat 'localhost' and '127.0.0.1' as different *sites*
    // (not just different origins), and SameSite=Lax cookies don't survive
    // cross-site fetch calls — only same-site ones. Always open the app
    // at http://127.0.0.1:5173, not http://localhost:5173.
    host: '127.0.0.1',
    port: 5173,
    // Mirrors production, where CloudFront serves the app and sends /api/*
    // to the backend: the browser only ever sees this one origin, so the
    // session cookie needs no cross-site settings and there's no CORS.
    proxy: {
      '/api': 'http://127.0.0.1:8000',
    },
  },
})
