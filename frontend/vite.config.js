import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
// 后端地址可用 BACKEND_URL 覆盖（默认 8080；本机 8080 被桌面版占用时可用 8081 等端口联调）
var backend = process.env.BACKEND_URL || 'http://localhost:8080';
export default defineConfig({
    plugins: [react()],
    server: {
        host: '0.0.0.0',
        port: 5173,
        proxy: {
            '/api': {
                target: backend,
                changeOrigin: true,
            },
            // PDF 页面缩略图（后端根路径挂载 /thumbnails，页面管理功能依赖）
            '/thumbnails': {
                target: backend,
                changeOrigin: true,
            }
        }
    }
});
