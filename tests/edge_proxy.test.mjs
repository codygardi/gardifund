import assert from 'node:assert/strict';
import test from 'node:test';
import gateway from '../deploy/worker/worker.js';

const origin = 'https://gardifund-view.example.workers.dev';
const token = 'synthetic-access-jwt-for-forwarding-tests';
const request = (path, init = {}) => new Request(origin + path, {
  ...init, headers: { 'Cf-Access-Jwt-Assertion': token, ...init.headers },
});

test('the gateway forbids controls, local modules and unrecognized paths before contacting the PC', async () => {
  const env = { GARDIFUND_ORIGIN: { fetch() { assert.fail('Denied request reached the PC'); } } };
  for (const path of ['/api/state', '/view/database', '/api/shadow/reset', '/healthz',
    '/assets/Investing-abcd.js', '/assets/Settings-abcd.js', '/assets/Data-abcd.js',
    '/assets/../api/state', '/assets/a%2f..%2fapi.js', '/assets/x.map']) {
    assert.equal((await gateway.fetch(request(path), env)).status, 403, path);
  }
  for (const method of ['POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS']) {
    assert.equal((await gateway.fetch(request('/view/overview', { method }), env)).status, 403, method);
  }
});

test('Access configuration and an assertion are required; the backend verifies the signed token', async () => {
  for (const value of ['', 'x'.repeat(16385)]) {
    const result = await gateway.fetch(request('/', { headers: { 'Cf-Access-Jwt-Assertion': value } }), {});
    assert.equal(result.status, 401);
    assert.equal(result.headers.get('Cache-Control'), 'no-store');
  }
  assert.equal((await gateway.fetch(request('/'), {})).status, 503);
  const env = { GARDIFUND_ORIGIN: { fetch: async () => Response.json({ detail: 'Invalid JWT' }, { status: 401 }) } };
  assert.equal((await gateway.fetch(request('/view/state'), env)).status, 401);
});

test('only read-only paths and essential headers reach the fixed viewing listener', async () => {
  const seen = [];
  const env = { GARDIFUND_ORIGIN: { fetch: async input => {
    seen.push(input);
    return Response.json({ safe: true }, { headers: {
      'Set-Cookie': 'local-session=private', 'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Credentials': 'true', 'Cache-Control': 'public, max-age=3600',
    } });
  } } };
  for (const path of ['/', '/scope', '/assets/index-abcd.js', '/assets/index-abcd.css',
    '/view/state', '/view/overview?mode=shadow', '/view/bars?symbol=SPY&interval=1h',
    '/view/events?after=5', '/view/sectors', '/view/attribution?start=2024-01-02', '/view/chart-performance?year=2024']) {
    const result = await gateway.fetch(request(path, { headers: {
      Cookie: 'private=value', Authorization: 'Bearer local-secret',
      'X-Forwarded-Host': '127.0.0.1:8765', Accept: 'application/json', 'Last-Event-ID': '42',
    } }), env);
    assert.equal(result.status, 200, path);
    assert.deepEqual(await result.json(), { safe: true });
    const forwarded = seen.at(-1);
    assert.equal(new URL(forwarded.url).origin, 'http://127.0.0.1:8766');
    assert.equal(new URL(forwarded.url).pathname, new URL(origin + path).pathname);
    assert.equal(new URL(forwarded.url).search, new URL(origin + path).search);
    assert.equal(forwarded.headers.get('Cf-Access-Jwt-Assertion'), token);
    assert.equal(forwarded.headers.get('Last-Event-ID'), '42');
    for (const header of ['Cookie', 'Authorization', 'X-Forwarded-Host']) assert.equal(forwarded.headers.get(header), null);
    for (const header of ['Set-Cookie', 'Access-Control-Allow-Origin', 'Access-Control-Allow-Credentials']) assert.equal(result.headers.get(header), null);
    assert.equal(result.headers.get('Cache-Control'), 'no-store');
  }
});

test('SSE is forwarded as a live stream rather than waiting for the response to finish', async () => {
  let controller;
  const stream = new ReadableStream({ start(value) { controller = value; } });
  const env = { GARDIFUND_ORIGIN: { fetch: async () => new Response(stream, { headers: { 'Content-Type': 'text/event-stream' } }) } };
  const result = await gateway.fetch(request('/view/events'), env);
  assert.equal(result.headers.get('Content-Type'), 'text/event-stream');
  const reader = result.body.getReader();
  controller.enqueue(new TextEncoder().encode(': connected\n\n'));
  assert.equal(new TextDecoder().decode((await reader.read()).value), ': connected\n\n');
  controller.enqueue(new TextEncoder().encode('id: 1\ndata: {"module":"overview"}\n\n'));
  assert.match(new TextDecoder().decode((await reader.read()).value), /"overview"/);
  controller.close();
  assert.equal((await reader.read()).done, true);
});

test('offline failures show a contact-admin page and keep data errors JSON', async () => {
  for (const fetch of [() => { throw new Error('private-address token=secret'); },
    () => new Response('private-address token=secret', { status: 502 })]) {
    const env = { GARDIFUND_ORIGIN: { fetch } };
    const offline = await gateway.fetch(request('/'), env);
    assert.equal(offline.status, 503);
    assert.match(offline.headers.get('Content-Type'), /^text\/html/);
    assert.equal(offline.headers.get('Cache-Control'), 'no-store');
    const page = await offline.text();
    assert.match(page, /Dashboard offline.*Contact the admin to start the GARDiFUND server/s);
    assert.doesNotMatch(page, /private-address|token=secret/);
    const head = await gateway.fetch(request('/', { method: 'HEAD' }), env);
    assert.equal(head.status, 503);
    assert.equal(await head.text(), '');
    const data = await gateway.fetch(request('/view/state'), env);
    assert.equal(data.status, 503);
    assert.match((await data.json()).detail, /Contact the admin/);
  }
});

test('origin redirects cannot disclose private connection details or set browser cookies', async () => {
  const redirected = await gateway.fetch(request('/'), { GARDIFUND_ORIGIN: { fetch: async () => Response.redirect('https://untrusted.example', 302) } });
  assert.equal(redirected.status, 502);
  assert.equal(redirected.headers.get('Location'), null);
});
