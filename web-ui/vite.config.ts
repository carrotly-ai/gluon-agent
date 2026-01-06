import path from 'node:path'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'
import { VitePWA } from 'vite-plugin-pwa'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    VitePWA({
      registerType: 'autoUpdate',
      includeAssets: ['gluon.svg', 'icons/*.png'],
      manifest: {
        name: 'Gluon Agent',
        short_name: 'Gluon',
        description: 'AI agent orchestrator for managing Claude Code sessions',
        theme_color: '#0c0c0c',
        background_color: '#0c0c0c',
        display: 'standalone',
        orientation: 'any',
        start_url: '/',
        scope: '/',
        icons: [
          { src: '/icons/icon-72.png', sizes: '72x72', type: 'image/png' },
          { src: '/icons/icon-96.png', sizes: '96x96', type: 'image/png' },
          { src: '/icons/icon-128.png', sizes: '128x128', type: 'image/png' },
          { src: '/icons/icon-144.png', sizes: '144x144', type: 'image/png' },
          { src: '/icons/icon-152.png', sizes: '152x152', type: 'image/png' },
          { src: '/icons/icon-192.png', sizes: '192x192', type: 'image/png' },
          { src: '/icons/icon-384.png', sizes: '384x384', type: 'image/png' },
          { src: '/icons/icon-512.png', sizes: '512x512', type: 'image/png' },
          {
            src: '/icons/icon-192-maskable.png',
            sizes: '192x192',
            type: 'image/png',
            purpose: 'maskable',
          },
          {
            src: '/icons/icon-512-maskable.png',
            sizes: '512x512',
            type: 'image/png',
            purpose: 'maskable',
          },
        ],
      },
      workbox: {
        // Cache static assets with cache-first strategy
        globPatterns: ['**/*.{js,css,html,ico,png,svg,woff,woff2}'],
        // SPA navigation fallback - serve index.html for all navigation requests
        // This ensures the app shell loads even when offline
        navigateFallback: '/index.html',
        // Don't use fallback for API routes - let them fail so the app can show offline state
        navigateFallbackDenylist: [/^\/api/],
        // Runtime caching for API calls
        runtimeCaching: [
          {
            // Version endpoint - NEVER cache, always hit network for update detection
            urlPattern: /^.*\/api\/version/,
            handler: 'NetworkOnly',
          },
          {
            // Cache API GET requests (projects, runs, etc.)
            urlPattern: /^.*\/api\/(projects|runs|workspaces|usage)/,
            handler: 'NetworkFirst',
            options: {
              cacheName: 'api-cache',
              expiration: {
                maxEntries: 100,
                maxAgeSeconds: 60 * 5, // 5 minutes
              },
              networkTimeoutSeconds: 3,
              cacheableResponse: {
                statuses: [0, 200],
              },
            },
          },
          {
            // Status endpoint for connectivity checks - always hit the network, never cache
            urlPattern: /^.*\/api\/status/,
            handler: 'NetworkFirst',
            options: {
              cacheName: 'api-status',
              networkTimeoutSeconds: 5,
              // Only cache successful responses (never cache errors)
              cacheableResponse: {
                statuses: [200],
              },
              expiration: {
                maxEntries: 1,
                maxAgeSeconds: 1, // Effectively don't cache
              },
            },
          },
          {
            // Cache fonts
            urlPattern: /^https:\/\/fonts\.googleapis\.com\/.*/i,
            handler: 'CacheFirst',
            options: {
              cacheName: 'google-fonts-cache',
              expiration: {
                maxEntries: 10,
                maxAgeSeconds: 60 * 60 * 24 * 365, // 1 year
              },
              cacheableResponse: {
                statuses: [0, 200],
              },
            },
          },
          {
            // Cache Google font files
            urlPattern: /^https:\/\/fonts\.gstatic\.com\/.*/i,
            handler: 'CacheFirst',
            options: {
              cacheName: 'google-fonts-webfonts',
              expiration: {
                maxEntries: 30,
                maxAgeSeconds: 60 * 60 * 24 * 365, // 1 year
              },
              cacheableResponse: {
                statuses: [0, 200],
              },
            },
          },
        ],
        // Skip waiting and claim clients immediately for faster updates
        skipWaiting: true,
        clientsClaim: true,
      },
      devOptions: {
        enabled: false, // Disable in dev to avoid caching issues
      },
    }),
  ],
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
