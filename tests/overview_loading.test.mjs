import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer } from 'vite';

test('Overview does not describe unavailable account data as an empty portfolio', async () => {
  const previousFetch = globalThis.fetch;
  globalThis.fetch = async input => {
    assert.equal(input, '/scope', 'Rendering this fixture must never reach a provider');
    return Response.json({ access_scope: 'remote' });
  };
  const server = await createServer({ logLevel: 'silent', server: { middlewareMode: true, hmr: false } });
  try {
    const { AppProvider } = await server.ssrLoadModule('/frontend/api.tsx');
    const { default: Overview } = await server.ssrLoadModule('/frontend/pages/Overview.tsx');
    const markup = renderToStaticMarkup(React.createElement(AppProvider, null,
      React.createElement(Overview, { navigate() {} })));
    assert.equal(markup.includes('0 connected accounts'), false, 'Missing account data must not become a zero account count');
    assert.equal(markup.includes('No open positions'), false, 'Missing positions must not become an empty portfolio');
    assert.equal(markup.includes('No recent orders'), false, 'Missing orders must not become an empty order history');
    assert.equal(markup.includes('Loading overview'), true);
  } finally {
    await server.close();
    globalThis.fetch = previousFetch;
  }
});
