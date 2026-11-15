import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readSessionResponse } from '../src/http.ts';

test('successful registration and login return validated session data', async () => {
  for (const status of [200, 201]) {
    assert.deepEqual(await readSessionResponse(Response.json({name:'alex',csrf:'session-token'}, {status})),
      {name:'alex',csrf:'session-token'});
  }
});

test('empty and non-JSON proxy failures produce a useful service error', async () => {
  for (const body of ['', '<html>Internal Server Error</html>']) {
    await assert.rejects(readSessionResponse(new Response(body, {status:500})), /Experience API debug session/);
  }
});

test('malformed successful responses cannot create a browser session', async () => {
  for (const body of ['', '{', '{}', '{"name":"alex"}', 'null']) {
    await assert.rejects(readSessionResponse(new Response(body)), /invalid response|invalid session/);
  }
});

test('safe validation and login errors remain visible', async () => {
  await assert.rejects(readSessionResponse(Response.json({detail:'Invalid username or password'}, {status:401})),
    /Invalid username or password/);
  await assert.rejects(readSessionResponse(Response.json({detail:'Username is already registered'}, {status:409})),
    /Username is already registered/);
});

test('server details are not displayed as authentication errors', async () => {
  await assert.rejects(readSessionResponse(Response.json({detail:'private provider exception'}, {status:503})),
    error => error.message.includes('unavailable') && !error.message.includes('private'));
});

test('dev:local defaults to the native Experience API; Docker and overrides remain supported', async () => {
  const previousTarget = process.env.EXPERIENCE_PROXY_TARGET;
  const previousEvent = process.env.npm_lifecycle_event;
  try {
    for (const [event, override, expected] of [
      ['dev:local', null, 'http://127.0.0.1:8020'],
      ['dev', null, 'http://experience:8000'],
      ['dev:local', 'http://custom:9000', 'http://custom:9000'],
    ]) {
      process.env.npm_lifecycle_event = event;
      if (override) process.env.EXPERIENCE_PROXY_TARGET = override;
      else delete process.env.EXPERIENCE_PROXY_TARGET;
      const {default:config} = await import('../vite.config.js?test=' + encodeURIComponent(event + expected));
      assert.equal(config.server.proxy['/api'], expected);
      assert.equal(config.server.proxy['/auth'], expected);
    }
  } finally {
    if (previousTarget === undefined) delete process.env.EXPERIENCE_PROXY_TARGET;
    else process.env.EXPERIENCE_PROXY_TARGET = previousTarget;
    if (previousEvent === undefined) delete process.env.npm_lifecycle_event;
    else process.env.npm_lifecycle_event = previousEvent;
  }
});
