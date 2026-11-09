// Thin read-only gateway. The Windows backend independently validates every JWT.
const VIEWS = new Set([
  '/view/state', '/view/overview', '/view/events', '/view/bars',
  '/view/sectors', '/view/attribution', '/view/chart-performance',
]);

function allowedPath(path) {
  if (path === '/' || path === '/scope' || VIEWS.has(path)) return true;
  if (!/^\/assets\/[A-Za-z0-9_-]+\.(?:js|css|svg|png|ico|woff2)$/.test(path)) return false;
  return !/^\/assets\/(?:Investing|Data|Settings)-/.test(path);
}

function headers() {
  return {
    'Cache-Control': 'no-store',
    'X-Content-Type-Options': 'nosniff',
    'Referrer-Policy': 'no-referrer',
    'Content-Security-Policy': "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
  };
}

function deny(status, detail) {
  return Response.json({ detail }, { status, headers: headers() });
}

function offline(request, path) {
  const detail = 'Contact the admin to start the GARDiFUND server, then try again.';
  if (path !== '/') return deny(503, detail);
  return new Response(request.method === 'HEAD' ? null : `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>GARDiFUND offline</title><style>
body{margin:0;padding:1.5rem;background:#eef2eb;color:#17291e;font:1rem/1.6 system-ui}
main{max-width:32rem;margin:12vh auto;padding:2rem;background:#fff;border:1px solid #d4dfd3;border-radius:1rem}
h1{line-height:1.2}a{color:#1b6544;font-weight:600}
</style></head><body><main><strong>GARDiFUND</strong><h1>Dashboard offline</h1><p>${detail}</p>
<a href="/">Try again</a></main></body></html>`, {
    status: 503, headers: { ...headers(), 'Content-Type': 'text/html; charset=utf-8', 'Retry-After': '30',
      'Content-Security-Policy': "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'" },
  });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (!['GET', 'HEAD'].includes(request.method) || !allowedPath(url.pathname)) {
      return deny(403, 'This gateway exposes read-only views only.');
    }
    const token = request.headers.get('Cf-Access-Jwt-Assertion');
    if (!token || token.length > 16384) {
      return deny(401, 'Cloudflare Access authentication is required.');
    }
    if (!env.GARDIFUND_ORIGIN || typeof env.GARDIFUND_ORIGIN.fetch !== 'function') {
      return deny(503, 'The private GARDiFUND connection is not configured.');
    }

    // A fixed VPC Service must be configured for 127.0.0.1:8766. Neither the
    // browser URL nor an incoming header can choose another local destination.
    const target = new URL('http://127.0.0.1:8766');
    target.pathname = url.pathname;
    target.search = url.search;
    const forwarded = new Headers({ 'Cf-Access-Jwt-Assertion': token });
    for (const name of ['Accept', 'Last-Event-ID']) {
      const value = request.headers.get(name);
      if (value !== null) forwarded.set(name, value);
    }
    try {
      const upstream = await env.GARDIFUND_ORIGIN.fetch(new Request(target, {
        method: request.method, headers: forwarded, signal: request.signal,
        redirect: 'manual',
      }));
      if ([502, 503, 504].includes(upstream.status)) {
        await upstream.body?.cancel();
        return offline(request, url.pathname);
      }
      if (upstream.status >= 300 && upstream.status < 400) {
        await upstream.body?.cancel();
        return deny(502, 'Unexpected redirect from the private workspace.');
      }
      const responseHeaders = new Headers(upstream.headers);
      responseHeaders.delete('Set-Cookie');
      responseHeaders.delete('Access-Control-Allow-Origin');
      responseHeaders.delete('Access-Control-Allow-Credentials');
      responseHeaders.set('Cache-Control', 'no-store');
      responseHeaders.set('X-Content-Type-Options', 'nosniff');
      responseHeaders.set('Referrer-Policy', 'no-referrer');
      // Preserve the response stream: buffering would break SSE synchronization.
      return new Response(request.method === 'HEAD' ? null : upstream.body, {
        status: upstream.status, statusText: upstream.statusText,
        headers: responseHeaders,
      });
    } catch {
      return offline(request, url.pathname);
    }
  },
};
