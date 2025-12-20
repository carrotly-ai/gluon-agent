import path from 'node:path'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  build: {
    // Output to FastAPI static directory
    outDir: '../src/gluon/web/dist',
    emptyOutDir: true,
  },
  server: {
    // Proxy API requests to FastAPI during development
    proxy: {
      '/api': {
        target: 'http://localhost:45866',
        changeOrigin: true,
        ws: true,
      },
    },
  },
})
