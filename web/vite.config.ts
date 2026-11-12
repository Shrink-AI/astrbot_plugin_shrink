import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  // static/ is a committed artifact served by the plugin, so a single hashed bundle is fine.
  build: { outDir: '../static', emptyOutDir: true, chunkSizeWarningLimit: 900 },
});