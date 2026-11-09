import assert from 'node:assert/strict';
import test from 'node:test';
import gateway from '../deploy/worker/worker.js';

const worker = 'https://synthetic.workers.dev';
const pages = 'https://codygardi.github.io';
const request = (path, init) => new Request(worker + path, init);

test('only public read routes reach the fixed origin', async () => {
  const env = { GARDIFUND_ORIGIN: { fetch() { assert.fail('Denied request reached the PC'); } } };
  for (const path of ['/api/state', '/view/database', '/view/positions', '/healthz', '/scope',
    '/assets/Settings-abcd.js', '/view/../api/state', '/view/state%2f..%2fapi.js']) {
    assert.equal((await gateway.fetch(request(path), env)).status, 403, path);
  }
  for (const method of ['POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS']) {
    assert.equal((await gateway.fetch(request('/view/overview', { method }), env)).status, 403, method);
  }
  const root = await gateway.fetch(request('/'), env);
  assert.equal(root.status, 302);
  assert.equal(root.headers.get('Location'), pages + '/gardifund/');
});

test('a Pages read needs no credentials and forwards only the viewing marker and cursor', async () => {
  let forwarded;
  const env = { GARDIFUND_ORIGIN: { fetch: async input => {
    forwarded = input;
    return Response.json({ access_scope: 'remote' }, { headers: {
      'Set-Cookie': 'local-session=private', 'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Credentials': 'true', 'Cache-Control': 'public, max-age=3600',
    } });
  } } };
  const result = await gateway.fetch(request('/view/state?after=5', { headers: {
    Origin: pages, Cookie: 'private=value', Authorization: 'Bearer local-secret',
    'Cf-Access-Jwt-Assertion': 'must-not-forward', 'X-Forwarded-Host': '127.0.0.1:8765', 'Last-Event-ID': '42',
  } }), env);
  assert.equal(result.status, 200);
  assert.deepEqual(await result.json(), { access_scope: 'remote' });
  assert.equal(forwarded.url, 'http://127.0.0.1:8766/view/state?after=5');
  assert.deepEqual(Object.fromEntries(forwarded.headers), { 'last-event-id': '42', 'x-gardifund-viewer': '1' });
  for (const header of ['Set-Cookie', 'Access-Control-Allow-Credentials']) assert.equal(result.headers.get(header), null);
  assert.equal(result.headers.get('Cache-Control'), 'no-store');
  assert.equal(result.headers.get('Access-Control-Allow-Origin'), pages);
});

test('only the Pages origin can preflight SSE reads', async () => {
  const env = { GARDIFUND_ORIGIN: { fetch() { assert.fail('Preflight reached the PC'); } } };
  const check = (origin, method, path = '/view/events') => gateway.fetch(request(path, {
    method: 'OPTIONS', headers: { Origin: origin, 'Access-Control-Request-Method': method,
      'Access-Control-Request-Headers': 'last-event-id' },
  }), env);
  const response = await check(pages, 'GET');
  assert.equal(response.status, 204);
  assert.equal(response.headers.get('Access-Control-Allow-Headers'), 'Last-Event-ID');
  assert.equal((await check('https://example.org', 'GET')).status, 403);
  assert.equal((await check(pages, 'POST')).status, 403);
  assert.equal((await check(pages, 'GET', '/api/state')).status, 403);
});

test('SSE is forwarded without buffering and supports cross-origin delivery', async () => {
  let controller;
  const stream = new ReadableStream({ start(value) { controller = value; } });
  const env = { GARDIFUND_ORIGIN: { fetch: async () => new Response(stream, { headers: { 'Content-Type': 'text/event-stream' } }) } };
  const result = await gateway.fetch(request('/view/events'), env);
  assert.equal(result.headers.get('Content-Type'), 'text/event-stream');
  assert.equal(result.headers.get('Access-Control-Allow-Origin'), pages);
  const reader = result.body.getReader();
  controller.enqueue(new TextEncoder().encode(': connected\n\n'));
  assert.equal(new TextDecoder().decode((await reader.read()).value), ': connected\n\n');
  controller.close();
  assert.equal((await reader.read()).done, true);
});

test('missing origin, upstream outages, redirects and HTML become a public-safe unavailable response', async () => {
  const source = 'private-address token=secret';
  const upstreams = [
    {},
    { GARDIFUND_ORIGIN: { fetch() { throw new Error(source); } } },
    { GARDIFUND_ORIGIN: { fetch: async () => new Response(source, { status: 502 }) } },
    { GARDIFUND_ORIGIN: { fetch: async () => new Response(source, { headers: { 'Content-Type': 'text/html' } }) } },
    { GARDIFUND_ORIGIN: { fetch: async () => Response.redirect('https://untrusted.example', 302) } },
  ];
  for (const env of upstreams) {
    const response = await gateway.fetch(request('/view/state'), env);
    assert.equal(response.status, 503);
    assert.equal(response.headers.get('Location'), null);
    assert.equal(response.headers.get('Access-Control-Allow-Origin'), pages);
    assert.doesNotMatch(await response.text(), /private-address|token=secret/);
  }
});
