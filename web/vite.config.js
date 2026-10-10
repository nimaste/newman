import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The hub serves the built files at "/", but relative asset URLs keep the
// bundle working if it is ever mounted under a sub-path.
export default defineConfig({
  base: './',
  plugins: [react()],
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
  server: {
    proxy: {
      // ws: live updates (api/ws) and camera video (api/entities/{id}/live) are websockets.
      '/api': { target: 'http://10.1.1.2:8093', changeOrigin: true, ws: true },
    },
  },
});
