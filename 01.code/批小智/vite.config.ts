import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

const securityHeaders = {
  'X-Content-Type-Options': 'nosniff',
  'Referrer-Policy': 'no-referrer',
  'X-Frame-Options': 'SAMEORIGIN',
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', '')
  const apiProxyTarget = env.VITE_API_PROXY_TARGET ?? 'http://127.0.0.1:8000'

  return {
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        // Keep framework/icon code cacheable across UI changes. The current
        // product is a single-page workbench, so this is the safe first step
        // before extracting whole route modules.
        manualChunks: {
          'react-vendor': ['react', 'react-dom'],
          'icons-vendor': ['@phosphor-icons/react'],
        },
      },
    },
  },
  server: {
    headers: securityHeaders,
    proxy: {
      '/api': {
        target: apiProxyTarget,
        changeOrigin: true,
      },
    },
  },
  preview: {
    headers: securityHeaders,
    proxy: {
      '/api': {
        target: apiProxyTarget,
        changeOrigin: true,
      },
    },
  },
  }
})
