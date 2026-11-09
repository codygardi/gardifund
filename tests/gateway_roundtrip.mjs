// Called by pytest against disposable, real local/remote Uvicorn sockets.
import assert from 'node:assert/strict';
import gateway from '../deploy/worker/worker.js';

const { QA_REMOTE_ORIGIN, QA_LOCAL_ORIGIN, QA_EVENT_CURSOR } = process.env;
assert.ok(QA_REMOTE_ORIGIN && QA_LOCAL_ORIGIN);
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
function view(path, method = 'GET', env = remote) {
  return gateway.fetch(new Request('https://synthetic.workers.dev' + path, {
    method, signal: AbortSignal.timeout(12000),
    headers: { Origin: 'https://codygardi.github.io' },
  }), env);
}

async function localSummary(mode) {
  const response = await fetch(`${QA_LOCAL_ORIGIN}/api/overview/summary?mode=${mode}`);
  assert.equal(response.status, 200);
  return response.json();
}

const scope = await view('/view/state');
assert.equal(scope.status, 200);
assert.equal(scope.headers.get('Access-Control-Allow-Origin'), 'https://codygardi.github.io');
assert.equal((await scope.json()).access_scope, 'remote');
const overview = await view('/view/overview?mode=live');
assert.equal(overview.status, 200);
const first = await overview.text();
const liveInitial = JSON.parse(first);
assert.equal(liveInitial.summary.equity, 1000);
assert.equal(liveInitial.summary.open_positions, 1);
assert.equal(liveInitial.summary.order_count, 1);
assert.deepEqual(await localSummary('live'), liveInitial);
assert.doesNotMatch(first, /AAA|SYNTHETIC-PRIVATE-ID|SYNTHETIC-PRIVATE-NOTE|never-forward/);
assert.equal(overview.headers.get('Cache-Control'), 'no-store');
const shadow = await view('/view/overview?mode=shadow');
assert.equal(shadow.status, 200);
const shadowInitial = await shadow.json();
assert.equal(shadowInitial.summary.initial, 10000);
assert.equal(shadowInitial.summary.order_count, 0);
assert.deepEqual(await localSummary('shadow'), shadowInitial);
const market = await view('/view/bars?symbol=SPY&interval=1d');
assert.equal(market.status, 200);
const firstBars = await market.text();
assert.equal(JSON.parse(firstBars).bars.at(-1).close, 101);
assert.doesNotMatch(firstBars, /SYNTHETIC-PRIVATE-SOURCE/);
assert.equal((await view('/view/bars?symbol=SPY&interval=30m')).status, 422);

// Even a wrongly configured VPC destination must not turn this gateway into a control API.
const wrongDestination = await view('/view/state', 'GET', binding(QA_LOCAL_ORIGIN));
assert.equal(wrongDestination.status, 403);
await wrongDestination.body?.cancel();
const beforeDenials = calls;
for (const [method, path] of [['GET', '/api/state'], ['GET', '/api/overview/summary'], ['POST', '/api/shadow/reset'], ['GET', '/view/database']]) {
  assert.equal((await view(path, method)).status, 403);
}
assert.equal(calls, beforeDenials, 'Forbidden requests must not reach the Windows server');
for (const path of ['/', '/assets/Settings-private.js', '/api/state', '/api/overview/summary', '/view/database']) {
  const response = await fetch(QA_REMOTE_ORIGIN + path);
  assert.equal(response.status, 403, 'The public listener must also reject non-view routes');
  await response.body?.cancel();
}

const stream = await view('/view/events?after=' + QA_EVENT_CURSOR);
assert.equal(stream.status, 200);
assert.match(stream.headers.get('Content-Type'), /text\/event-stream/);
const reader = stream.body.getReader();
const decoder = new TextDecoder();
let text = '';
try {
  const readEvents = () => text.split('\n\n').slice(0, -1)
    .flatMap(frame => frame.split('\n').filter(line => line.startsWith('data: ')))
    .map(line => JSON.parse(line.slice(6)));
  let events = [];
  while (events.filter(event => event.module === 'overview').length < 2 || !events.some(event => event.module === 'charts')) {
    const item = await reader.read();
    assert.equal(item.done, false, 'The real event stream ended before the update');
    text += decoder.decode(item.value, { stream: true });
    assert.ok(text.length < 16384);
    events = readEvents();
  }
  for (const event of events.filter(event => event.module === 'overview')) {
    assert.equal(event.action, 'updated');
    assert.equal(event.payload, null);
  }
  const marketEvent = events.find(event => event.module === 'charts');
  assert.equal(marketEvent.action, 'market_updated');
  assert.deepEqual(marketEvent.payload, { symbol: 'SPY', interval: '1d' });
  assert.doesNotMatch(text, /SYNTHETIC-PRIVATE-ID|SYNTHETIC-PRIVATE-NOTE/);
} finally {
  await reader.cancel();
}
const updated = await view('/view/overview?mode=live');
assert.equal(updated.status, 200);
const liveUpdated = await updated.json();
assert.equal(liveUpdated.summary.equity, 1234);
assert.deepEqual(await localSummary('live'), liveUpdated);
const shadowResponse = await view('/view/overview?mode=shadow');
assert.equal(shadowResponse.status, 200);
const shadowUpdated = await shadowResponse.json();
assert.equal(shadowUpdated.summary.open_positions, 1);
assert.equal(shadowUpdated.summary.order_count, 1);
assert.ok(!('positions' in shadowUpdated) && !('orders' in shadowUpdated));
assert.deepEqual(await localSummary('shadow'), shadowUpdated);
const ranking = await view('/view/chart-performance?mode=market&year=2024&month=1');
assert.equal(ranking.status, 200);
const rankingData = await ranking.json();
assert.equal(rankingData.rows[0].symbol, 'SPY');
assert.equal(rankingData.series[0].bars.at(-1).close, 103);
console.log(JSON.stringify({ public_gateway_roundtrip: true, overview_sse_update: true, charts_sse_update: true, local_controls_denied: true }));
