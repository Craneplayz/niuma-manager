import { defineConfig } from 'vite'

export default defineConfig({
  build: { rollupOptions: { input: { app: 'index.html', admin: 'admin.html' } } },
  server: {
    port: Number(process.env.NIUMA_WEB_PORT || 1331),
    strictPort: true,
    proxy: { '/api': 'http://127.0.0.1:' + (process.env.NIUMA_PORT || 1332) },
  },
})
