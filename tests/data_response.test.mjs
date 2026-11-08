import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import ts from 'typescript';

const source = await readFile(new URL('../frontend/dataResponse.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2021 } }).outputText;
const { readWorkspaceResponse } = await import('data:text/javascript;base64,' + Buffer.from(compiled).toString('base64'));

test('valid workspace JSON retains known zeros and empty record lists', async () => {
  const data = { summary: { cash: 0, open_positions: 0 }, positions: [] };
  assert.deepEqual(await readWorkspaceResponse(Response.json(data)), data);
});

test('HTML and incomplete responses never become a successful empty data object', async () => {
  for (const response of [new Response('<!doctype html><title>Static page</title>'),
    Response.json({}), Response.json(null), Response.json([]), Response.json(0)]) {
    await assert.rejects(() => readWorkspaceResponse(response), /data connection|data is unavailable/);
  }
});

test('structured API errors are preserved without leaking raw HTML', async () => {
  await assert.rejects(() => readWorkspaceResponse(Response.json({ detail: 'Access authentication required' }, { status: 401 })),
    /Access authentication required/);
  await assert.rejects(() => readWorkspaceResponse(new Response('<html>private upstream diagnostic</html>', { status: 502 })),
    error => !error.message.includes('private upstream diagnostic') && !error.message.includes('Unexpected token'));
});

test('a real sign-in redirect produces a session message instead of fake account data', async () => {
  const server = createServer((request, response) => {
    if (request.url === '/view/overview') response.writeHead(302, { Location: '/login' }).end();
    else response.writeHead(200, { 'Content-Type': 'text/html' }).end('<!doctype html><title>Sign in</title>');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  try {
    const response = await fetch(`http://127.0.0.1:${server.address().port}/view/overview`);
    assert.equal(response.redirected, true);
    await assert.rejects(() => readWorkspaceResponse(response), /sign-in session needs attention/);
  } finally {
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
  }
});
