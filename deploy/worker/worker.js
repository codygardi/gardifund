// Public read-only projections from a fixed Tunnel VPC Service.
const PAGES = 'https://codygardi.github.io';
const VIEWS = new Set(['/view/state', '/view/overview', '/view/events']);
const HEADERS = {
  'Access-Control-Allow-Origin': PAGES,
  'Cache-Control': 'no-store',
  'X-Content-Type-Options': 'nosniff',
  'Referrer-Policy': 'no-referrer',
  'Content-Security-Policy': "default-src 'none'; frame-ancestors 'none'",
};

function deny(status, detail) {
  return Response.json({ detail }, { status, headers: HEADERS });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === 'OPTIONS' && VIEWS.has(url.pathname) && request.headers.get('Origin') === PAGES
        && ['GET', 'HEAD'].includes(request.headers.get('Access-Control-Request-Method'))) {
      return new Response(null, { status: 204, headers: { ...HEADERS,
        'Access-Control-Allow-Methods': 'GET, HEAD', 'Access-Control-Allow-Headers': 'Last-Event-ID' } });
    }
    if (!['GET', 'HEAD'].includes(request.method)) return deny(403, 'Read-only dashboard.');
    if (url.pathname === '/') return Response.redirect(PAGES + '/gardifund/', 302);
    if (!VIEWS.has(url.pathname)) return deny(403, 'Read-only dashboard.');
    const origin = env.GARDIFUND_ORIGIN;
    if (!origin || typeof origin.fetch !== 'function') return deny(503, 'Dashboard connection unavailable.');

    const target = new URL('http://127.0.0.1:8766');
    target.pathname = url.pathname;
    target.search = url.search;
    const forwarded = new Headers({ 'X-Gardifund-Viewer': '1' });
    const cursor = request.headers.get('Last-Event-ID');
    if (cursor !== null) forwarded.set('Last-Event-ID', cursor);
    try {
      const upstream = await origin.fetch(new Request(target, {
        method: request.method, headers: forwarded, signal: request.signal, redirect: 'manual',
      }));
      const mediaType = upstream.headers.get('Content-Type')?.split(';')[0];
      const expected = url.pathname === '/view/events' && upstream.ok ? 'text/event-stream' : 'application/json';
      if (upstream.status >= 500 || (upstream.status >= 300 && upstream.status < 400) || mediaType !== expected) {
        await upstream.body?.cancel();
        return deny(503, 'Contact the admin to start the GARDiFUND server.');
      }
      return new Response(request.method === 'HEAD' ? null : upstream.body, {
        status: upstream.status, headers: { ...HEADERS, 'Content-Type': upstream.headers.get('Content-Type') },
      });
    } catch {
      return deny(503, 'Contact the admin to start the GARDiFUND server.');
    }
  },
};
