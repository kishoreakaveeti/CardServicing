import { createLogger, defineConfig } from 'vite';
const logger = createLogger();
const error = logger.error.bind(logger);
logger.error = (message, options) => error(message.replace(/([?&](?:code|state|session_state)=)[^&\s]*/gi, '$1[redacted]'), options);
export default defineConfig({
  customLogger: logger,
  build: { sourcemap: true },
  server: { port: 8080, strictPort: true, proxy: { '/api': process.env.EXPERIENCE_PROXY_TARGET || 'http://experience:8000', '/auth': process.env.EXPERIENCE_PROXY_TARGET || 'http://experience:8000' } }
});
