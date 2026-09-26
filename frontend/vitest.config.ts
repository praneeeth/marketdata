import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
      '@candlewise/api': path.resolve(__dirname, './packages/api/src'),
      '@candlewise/base-ui': path.resolve(__dirname, './packages/base-ui/src'),
      '@candlewise/biz-ui': path.resolve(__dirname, './packages/biz-ui/src'),
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
  },
})
