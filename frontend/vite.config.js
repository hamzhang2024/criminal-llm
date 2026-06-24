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
            }
        }
    },
    build: {
        rollupOptions: {
            output: {
                // vendor 分包：稳定依赖独立成 chunk，长期缓存（应用代码变更不影响 vendor 缓存）
                manualChunks: {
                    'react-vendor': ['react', 'react-dom', 'react-router-dom'],
                    'tauri-vendor': ['@tauri-apps/api', '@tauri-apps/plugin-shell'],
                },
            },
        },
    },
});
