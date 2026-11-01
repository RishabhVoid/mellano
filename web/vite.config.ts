import { execFile } from 'node:child_process';
import { defineConfig, type Plugin } from 'vite';

function openChrome(): Plugin {
  return {
    name: 'otec-open-chrome',
    configureServer(server) {
      server.httpServer?.once('listening', () => {
        if (process.platform !== 'darwin' || process.env.OTEC_NO_OPEN === '1') return;
        const address = server.httpServer?.address();
        if (!address || typeof address === 'string') return;
        execFile('open', ['-a', process.env.OTEC_BROWSER_APP || 'Google Chrome', `http://127.0.0.1:${address.port}`], (error) => {
          if (error) server.config.logger.warn('Open the printed Local URL in your browser.');
        });
      });
    },
  };
}

export default defineConfig({
  plugins: [openChrome()],
  // Never discover/read environment files implicitly. Process variables only.
  envDir: false,
  // The single Python host serves immutable data directly; do not copy gigabytes on startup.
  build: { copyPublicDir: false },
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: { '/api': { target: 'http://127.0.0.1:8000', rewrite: (path) => path.replace(/^\/api/, '') } },
    watch: { ignored: ['**/*.env*', '**/public/data/**', '**/public/assets/**'] },
    fs: { deny: ['**/*.env*', '**/*.{crt,pem}', '**/.git/**'] },
  },
  preview: { port: 4173, strictPort: true },
});
