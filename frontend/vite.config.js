import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
    plugins: [react()],
    server: {
        host: process.env.HOST || true, // Listen on all local IPs or specify host
        port: parseInt(process.env.PORT) || 5173,
        proxy: {
            '/api': {
                target: `http://${process.env.BACKEND_HOST || 'localhost'}:${process.env.BACKEND_PORT || 8000}`,
                changeOrigin: true,
            },
            '/auth': {
                target: `http://${process.env.BACKEND_HOST || 'localhost'}:${process.env.BACKEND_PORT || 8000}`,
                changeOrigin: true,
            },
        },
    },
})
