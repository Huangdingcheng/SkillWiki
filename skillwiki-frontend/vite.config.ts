import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

const apiTarget = process.env.SKILLOS_API_TARGET ?? 'http://127.0.0.1:8000'
const wsTarget = apiTarget.replace(/^http/, 'ws')

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': { target: apiTarget, changeOrigin: true },
      '/ws': { target: wsTarget, ws: true },
    },
  },
})
