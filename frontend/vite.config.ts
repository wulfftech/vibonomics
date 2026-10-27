import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

/** The backend the dev server proxies to (`python -m vibonomics` listens on 2289 by default). */
const apiTarget = 'http://localhost:2289';

export default defineConfig({
  // Relative asset URLs so the build works under any URL base; index.html carries <base href>.
  base: './',
  plugins: [react()],
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    chunkSizeWarningLimit: 1200,
  },
  server: {
    port: 5173,
    proxy: {
      // Includes the server-sent events stream at /api/events; the proxy passes it through unbuffered.
      '/api': { target: apiTarget, changeOrigin: true },
      '/ping': { target: apiTarget, changeOrigin: true },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    css: false,
    testTimeout: 15000,
  },
});
