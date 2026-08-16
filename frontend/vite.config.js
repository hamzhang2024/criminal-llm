import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig({
    plugins: [react()],
    server: {
        host: '0.0.0.0',
        port: 5173,
        proxy: {
            '/api': {
                target: 'http://localhost:8080',
                changeOrigin: true,
            },
            // PDF 页面缩略图（后端根路径挂载 /thumbnails，页面管理功能依赖）
            '/thumbnails': {
                target: 'http://localhost:8080',
                changeOrigin: true,
            }
        }
    }
});
