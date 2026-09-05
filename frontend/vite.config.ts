import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The API and the SSE stream are served by the FastAPI app on 8765. In dev,
// Vite proxies to it so the browser sees a single origin; `npm run build`
// emits dist/, which FastAPI then serves directly.
//
// SSE passes through the dev proxy unchanged: FastAPI's StreamingResponse
// sends no content-length, so there is nothing for the proxy to buffer.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8765',
        changeOrigin: true,
      },
    },
  },
  build: { outDir: 'dist', emptyOutDir: true },
});
