import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';

// In development the backend runs on :8080 (see Makefile `dev`).
export default defineConfig({
  plugins: [svelte()],
  server: {
    proxy: { '/api': 'http://127.0.0.1:8080', '/mcp': 'http://127.0.0.1:8080' },
  },
});
