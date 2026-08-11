import path from 'node:path'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react-swc'
import tailwindcss from '@tailwindcss/vite'

const DEFAULT_ATLAS_API_BASE_URL = 'http://127.0.0.1:8000'
const atlasApiBaseUrl =
  process.env.VITE_ATLAS_API_BASE_URL ?? DEFAULT_ATLAS_API_BASE_URL

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    host: '127.0.0.1',
    proxy: {
      '/api': {
        target: atlasApiBaseUrl,
        changeOrigin: false,
      },
    },
  },
  preview: {
    host: '127.0.0.1',
    proxy: {
      '/api': {
        target: atlasApiBaseUrl,
        changeOrigin: false,
      },
    },
  },
  build: {
    chunkSizeWarningLimit: 700,
  },
})
