import { createLogger, defineConfig } from 'vite';
const logger = createLogger();
const error = logger.error.bind(logger);
logger.error = (message, options) => error(message.replace(/([?&](?:code|state|session_state)=)[^&\s]*/gi, '$1[redacted]'), options);
const experienceTarget = process.env.EXPERIENCE_PROXY_TARGET ||
  (process.env.npm_lifecycle_event === 'dev:local' ? 'http://127.0.0.1:8020' : 'http://experience:8000');
export default defineConfig({
  customLogger: logger,
  build: { sourcemap: true },
  server: { port: 8080, strictPort: true, proxy: { '/api': experienceTarget, '/auth': experienceTarget } }
});
