import { copyFileSync } from 'node:fs';
import { fileURLToPath, URL } from 'node:url';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

const output = fileURLToPath(new URL('../reelrelay/web/', import.meta.url));

export default defineConfig({
  plugins: [
    react(),
    {
      name: 'settings-entry',
      apply: 'build',
      closeBundle() {
        copyFileSync(output + 'index.html', output + 'setup.html');
      },
    },
  ],
  build: { outDir: output, emptyOutDir: true, sourcemap: false },
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8765',
      '/media': 'http://127.0.0.1:8765',
      '/health': 'http://127.0.0.1:8765',
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    restoreMocks: true,
    clearMocks: true,
  },
});
