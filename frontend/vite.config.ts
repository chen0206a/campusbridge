import { loadEnv } from 'vite';
import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const proxy = { '/api': { target: env.CAMPUSBRIDGE_PROXY_TARGET || 'http://127.0.0.1:8015', changeOrigin: true } };
  return { plugins: [react()], server: { port: 5173, strictPort: true, proxy }, preview: { proxy },
    test: { environment: 'jsdom', globals: true, setupFiles: ['./src/test/setup.ts'], restoreMocks: true } };
});
