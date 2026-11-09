// Called by pytest against disposable, real local/remote Uvicorn sockets.
import assert from 'node:assert/strict';
import gateway from '../deploy/worker/worker.js';

const { QA_REMOTE_ORIGIN, QA_LOCAL_ORIGIN, QA_ACCESS_JWT, QA_EXPIRED_JWT, QA_WRONG_AUDIENCE_JWT, QA_EVENT_CURSOR } = process.env;
assert.ok(QA_REMOTE_ORIGIN && QA_LOCAL_ORIGIN && QA_ACCESS_JWT);
let calls = 0;
function binding(destination) {
  return { GARDIFUND_ORIGIN: { fetch(input) {
    calls += 1;
    assert.equal(new URL(input.url).origin, 'http://127.0.0.1:8766');
    const url = new URL(input.url);
    // A VPC Service selects its configured host/port, not the request URL.
    url.host = new URL(destination).host;
    return fetch(new Request(url, input));
  } } };
}
const remote = binding(QA_REMOTE_ORIGIN);
function view(path, token = QA_ACCESS_JWT, method = 'GET', env = remote) {
  return gateway.fetch(new Request('https://synthetic.workers.dev' + path, {
    method, signal: AbortSignal.timeout(12000),
    headers: { 'Cf-Access-Jwt-Assertion': token, Cookie: 'local-private-cookie=never-forward', Authorization: 'Bearer never-forward' },
  }), env);
}

const scope = await view('/scope');
assert.equal(scope.status, 200);
assert.equal((await scope.json()).access_scope, 'remote');
const overview = await view('/view/overview?mode=live');
assert.equal(overview.status, 200);
const first = await overview.text();
assert.equal(JSON.parse(first).summary.equity, 1000);
assert.equal(JSON.parse(first).summary.open_positions, 1);
assert.doesNotMatch(first, /AAA|SYNTHETIC-PRIVATE-ID|SYNTHETIC-PRIVATE-NOTE|never-forward/);
assert.equal(overview.headers.get('Cache-Control'), 'no-store');
const shadow = await view('/view/overview?mode=shadow');
assert.equal(shadow.status, 200);
assert.equal((await shadow.json()).summary.initial, 10000);
const market = await view('/view/bars?symbol=SPY&interval=1d');
assert.equal(market.status, 200);
const firstBars = await market.text();
assert.equal(JSON.parse(firstBars).bars.at(-1).close, 101);
assert.doesNotMatch(firstBars, /SYNTHETIC-PRIVATE-SOURCE/);

const parts = QA_ACCESS_JWT.split('.');
parts[2] = (parts[2][0] === 'a' ? 'b' : 'a') + parts[2].slice(1);
for (const token of [parts.join('.'), QA_EXPIRED_JWT, QA_WRONG_AUDIENCE_JWT]) {
  const response = await view('/view/overview?mode=live', token);
  assert.equal(response.status, 401, 'The actual backend must reject an invalid signed token');
  await response.body?.cancel();
}
// Even a wrongly configured VPC destination must not turn this gateway into a control API.
const wrongDestination = await view('/scope', QA_ACCESS_JWT, 'GET', binding(QA_LOCAL_ORIGIN));
assert.equal(wrongDestination.status, 403);
await wrongDestination.body?.cancel();
const beforeDenials = calls;
for (const [method, path] of [['GET', '/api/state'], ['POST', '/api/shadow/reset'], ['GET', '/view/database']]) {
  assert.equal((await view(path, QA_ACCESS_JWT, method)).status, 403);
}
assert.equal(calls, beforeDenials, 'Forbidden requests must not reach the Windows server');

const stream = await view('/view/events?after=' + QA_EVENT_CURSOR);
assert.equal(stream.status, 200);
assert.match(stream.headers.get('Content-Type'), /text\/event-stream/);
const reader = stream.body.getReader();
const decoder = new TextDecoder();
let text = '';
try {
  const readEvents = () => text.split('\n\n').slice(0, -1).flatMap(frame => frame.split('\n').filter(line => line.startsWith('data: ')));
  while (readEvents().length < 2) {
    const item = await reader.read();
    assert.equal(item.done, false, 'The real event stream ended before the update');
    text += decoder.decode(item.value, { stream: true });
    assert.ok(text.length < 16384);
  }
  const events = readEvents().map(line => JSON.parse(line.slice(6)));
  const overviewEvent = events.find(event => event.module === 'overview');
  assert.equal(overviewEvent.action, 'updated');
  assert.equal(overviewEvent.payload, null);
  const marketEvent = events.find(event => event.module === 'charts');
  assert.equal(marketEvent.action, 'market_updated');
  assert.deepEqual(marketEvent.payload, { symbol: 'SPY', interval: '1d' });
  assert.doesNotMatch(text, /SYNTHETIC-PRIVATE-ID|SYNTHETIC-PRIVATE-NOTE/);
} finally {
  await reader.cancel();
}
const updated = await view('/view/overview?mode=live');
assert.equal(updated.status, 200);
assert.equal((await updated.json()).summary.equity, 1234);
const ranking = await view('/view/chart-performance?mode=market&year=2024&month=1');
assert.equal(ranking.status, 200);
const rankingData = await ranking.json();
assert.equal(rankingData.rows[0].symbol, 'SPY');
assert.equal(rankingData.series[0].bars.at(-1).close, 103);
console.log(JSON.stringify({ authenticated_gateway_roundtrip: true, invalid_tokens_rejected: 3, overview_sse_update: true, charts_sse_update: true, local_controls_denied: true }));
