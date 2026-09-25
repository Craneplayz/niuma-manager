import { defineConfig } from 'vite'

export default defineConfig({
  server: {
    port: 1331,
    strictPort: true,
    proxy: { '/api': 'http://127.0.0.1:1332' },
  },
})
