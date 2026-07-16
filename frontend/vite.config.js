import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5555,
    strictPort: true,
    // 允许通过任意 IP / 主机名访问（局域网「全局」访问）
    allowedHosts: true,
    proxy: { '/api': 'http://127.0.0.1:8888' },
  },
})
