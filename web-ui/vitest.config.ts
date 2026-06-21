import path from 'node:path'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// Standalone test config — deliberately omits the PWA + Tailwind plugins from
// vite.config.ts (irrelevant in jsdom and slow to set up). Mirrors only the
// `@/` path alias so component imports resolve the same way they do at build.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    css: false,
  },
})
