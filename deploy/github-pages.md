# GitHub Pages and the live workspace

Repository: `https://github.com/codygardi/gardifund`

Public entry page: `https://codygardi.github.io/gardifund/`

GitHub Pages is the public launch page. Its button opens the Cloudflare-protected WAN app, which has exactly three navigation items: Overview, Charts, and Back Test. The full React app and FastAPI backend continue to run on Windows. Local investing, settings and database controls are not part of the public Pages artifact.

```text
GitHub Pages launch page
  -> Access-protected gardifund.codygardi22.workers.dev
  -> thin read-only Worker
  -> private Cloudflare Tunnel / VPC Service
  -> local read-only listener, 127.0.0.1:8766
  -> cached Overview and Charts + SSE
```

Overview and Charts read selected, sanitized data from the local SQLite/Parquet store through JSON endpoints and SSE while the Windows app and Tunnel are running. The viewing service reads cached data; remote viewers cannot call Tradier, submit orders or administer the database. Back Test is a placeholder and has no results to synchronize yet. The raw database files stay local. Synchronization does not require pushing account data or commits to GitHub. Changes to the app's source are versioned in Git; the public launch page changes only when its static files or dashboard URL are republished. These are separate operations.

Use the same protected shared link on another LAN device or on the Internet. Only the Windows PC gets the full seven-module workspace at `http://127.0.0.1:8765/`. The launch page and new workspaces default to light mode; a saved dark-mode choice remains available.

## Publish and update the entry page

The `Verify and publish` workflow runs Windows integration/security tests, the UI build, gateway tests and the bounded synthetic load test. On `main`, it then builds and deploys **only** `pages-dist/` to GitHub Pages. This artifact contains five files: HTML, CSS, JavaScript, the public dashboard URL configuration, and `.nojekyll`. It contains no `.env`, app bundle, positions, balances, snapshots, backup, or production data.

The site's default state is **Dashboard connection pending**. It never claims the local app is online merely because a URL exists.

After configuring Cloudflare using [the free-plan setup](free-cloudflare.md), open the repository's **Settings > Secrets and variables > Actions > Variables**. Add a repository variable named:

```text
GARDIFUND_DASHBOARD_URL
```

Set it to `https://gardifund.codygardi22.workers.dev/`. This address is public configuration, not a token. Do not put API keys or tunnel credentials into this variable. Run **Actions > Verify and publish > Run workflow** to republish the launch page. Test the button from another network. The address must run the gateway with a VPC Service and Access policy; a static copy of the app alone cannot synchronize data.

GitHub Pages must use **GitHub Actions** as its build source. GitHub Free supports Pages from public repositories; this repository was explicitly requested to be public. See [Pages publishing workflows](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages).

## Local preview

From the workspace:

```powershell
.\.venv\Scripts\python.exe scripts\build-pages.py
.\.venv\Scripts\python.exe -m http.server 8080 --bind 127.0.0.1 --directory pages-dist
```

Open `http://127.0.0.1:8080/`. Stop this preview with Ctrl+C. The preview is independent of the normal app on port 8765. The builder reads only its public URL from the process environment; it never loads `.env`.

## Cost and data limits

The public entry page and public-repository Actions use GitHub's free services within their published limits. The no-domain Cloudflare route uses Workers Free, Access Free and Workers VPC. **VPC is currently free during beta; its future pricing is not guaranteed.** Stay on free plans and stop if the dashboard requests an upgrade. No paid plan, domain purchase or automatic billing upgrade is configured by this repository. See [GitHub Pages limits](https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits) and [the Cloudflare setup and limits](free-cloudflare.md).

Hosting does not create missing market history. The app uses available Tradier/Yahoo data and reports missing historical coverage and constituents. It does not buy data or invent records to fill the gaps.
