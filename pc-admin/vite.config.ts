import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

const proxyTarget = (globalThis as typeof globalThis & {
  process?: { env?: { MALL_API_PROXY_TARGET?: string } }
}).process?.env?.MALL_API_PROXY_TARGET ?? 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: { '/api': proxyTarget },
  },
})
