import assert from 'node:assert/strict';
import { mkdtemp, readFile, readdir, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import test from 'node:test';
import { build, loadConfigFromFile } from 'vite';

test('the Pages artifact is relative, read-only, and contains only static browser assets', async () => {
  const parent = resolve(tmpdir());
  const output = await mkdtemp(join(parent, 'gardifund-pages-'));
  assert.equal(dirname(output), parent);
  const variable = 'GARDIFUND_DASHBOARD_URL';
  const previous = process.env[variable];
  process.env[variable] = 'https://gardifund.demo.workers.dev/';
  try {
    await build({ mode: 'pages', logLevel: 'silent', build: { outDir: output, emptyOutDir: false } });
    assert.deepEqual((await readdir(output)).sort(), ['.nojekyll', 'assets', 'index.html']);
    const html = await readFile(join(output, 'index.html'), 'utf8');
    assert.match(html, /src="\.\/assets\//);
    assert.match(html, /connect-src https:\/\/gardifund\.demo\.workers\.dev/);
    const assets = await readdir(join(output, 'assets'));
    assert.ok(assets.some(name => name.startsWith('Overview-')));
    assert.ok(assets.every(name => /\.(js|css)$/.test(name) && !/^(Investing|Settings|Data|ShadowReset|OverviewDetails|Charts|Backtest)-/.test(name)));
    const js = (await Promise.all(assets.filter(name => name.endsWith('.js'))
      .map(name => readFile(join(output, 'assets', name), 'utf8')))).join('\n');
    assert.match(js, /Contact the admin to start the GARDiFUND server/);
    assert.match(js, /label:"Research"/);
    assert.doesNotMatch(js, /PRODUCTION_API_KEY|CF_ACCESS_AUD|127\.0\.0\.1:8765|Sign in through Cloudflare Access/);
    assert.doesNotMatch(js, /Back Test Engine|label:"Snapshot"|\/backtest\/|\/chart-performance/);
  } finally {
    if (previous === undefined) delete process.env[variable];
    else process.env[variable] = previous;
    await rm(output, { recursive: true, force: true });
  }
});

test('Pages configuration rejects secret-bearing and non-Worker URLs', async () => {
  const variable = 'GARDIFUND_DASHBOARD_URL';
  const previous = process.env[variable];
  try {
    for (const value of ['http://gardifund.demo.workers.dev', 'https://127.0.0.1:8765',
      'https://user:secret@gardifund.demo.workers.dev', 'https://gardifund.demo.workers.dev/?token=secret']) {
      process.env[variable] = value;
      await assert.rejects(loadConfigFromFile({ command: 'build', mode: 'pages' }, undefined, undefined, 'silent'), /HTTPS root/);
    }
  } finally {
    if (previous === undefined) delete process.env[variable];
    else process.env[variable] = previous;
  }
});
