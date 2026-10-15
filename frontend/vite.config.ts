/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

const apiTarget = process.env.VITE_API_PROXY_TARGET ?? 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { '/api': { target: apiTarget, changeOrigin: false } },
  },
  test: {
    environment: 'jsdom',
    globals: false,
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    // Adversarial probe tests run only with PROBES=1 (see CLAUDE.md).
    exclude: process.env.PROBES ? ['node_modules/**', 'e2e/**'] : ['src/__probes__/**', 'node_modules/**', 'e2e/**'],
    css: false,
  },
})
