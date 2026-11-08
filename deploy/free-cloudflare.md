# Cloudflare setup without purchasing a domain

This route keeps the Windows modular monolith and adds only a small read-only gateway. A free `workers.dev` hostname is protected by Access; a VPC Service connects it to the existing private Tunnel and port 8766. GitHub Pages opens that hostname. No custom domain is needed for this route.

Cloudflare's current [Workers VPC documentation](https://developers.cloudflare.com/workers-vpc/get-started/) makes VPC available free to all Workers plans **during beta**. Its future price cannot be guaranteed. Use Workers Free and Access Free, and do not enable a paid plan. Workers Free currently allows 100,000 requests per day; traffic and SSE reconnects count toward usage. Exceeding free limits can make the site unavailable. See [Workers limits](https://developers.cloudflare.com/workers/platform/limits/). This repository configures no paid services or automatic upgrade.

## 1. Create a private Tunnel

Create a Cloudflare account and finish Zero Trust setup. In **Networking > Tunnels**, create a named tunnel for this Windows PC and follow the Windows connector instructions. Use the existing verified `.tools\cloudflared.exe` when it is not on PATH. The service-install command contains a private tunnel token; run it locally and keep it out of chat and Git.

Do **not** add a published application/DNS route. This tunnel will serve a VPC Service instead. Use QUIC transport; Workers VPC requires outbound UDP on port 7844. A Quick Tunnel is unsuitable: its hostname changes and it does not support SSE. See [VPC troubleshooting](https://developers.cloudflare.com/workers-vpc/reference/troubleshooting/) and [Quick Tunnel limits](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/).

## 2. Define a service for the viewing port only

Log in using Cloudflare's Wrangler CLI on your own PC. Use an account with the Connectivity Directory Admin role. Create one HTTP VPC Service for this tunnel and **only** the viewing destination:

```powershell
npx wrangler login
npx wrangler vpc service create gardifund-view --type http --tunnel-id YOUR-TUNNEL-UUID --ipv4 127.0.0.1 --http-port 8766
```

Record the returned service ID. Never choose port 8765. Use a fixed VPC **Service**, not a broad VPC Network binding that could reach other local services. The Worker code also fixes its destination to port 8766. See [VPC service configuration](https://developers.cloudflare.com/workers-vpc/configuration/vpc-services/) and [Wrangler VPC commands](https://developers.cloudflare.com/workers/wrangler/commands/vpc/).

## 3. Deploy the gateway and protect its hostname

Copy `deploy/worker/wrangler.example.json` to `deploy/worker/wrangler.local.json`, which is ignored by Git. Replace the service-ID placeholder with your ID. The gateway name defaults to `gardifund-view`; its address will be `https://gardifund-view.YOUR-SUBDOMAIN.workers.dev`.

```powershell
npx wrangler deploy --config deploy/worker/wrangler.local.json
```

Under **Workers & Pages > gardifund-view > Settings > Domains & Routes**, enable Cloudflare Access for the production `workers.dev` address. Protect all production traffic and keep preview URLs disabled. Restrict the Allow policy to the identities you intend to share with. Configure MFA at the Access application and check that no matching Allow policy exempts it. See [Access for Workers](https://developers.cloudflare.com/workers/configuration/cloudflare-access/) and [MFA requirements](https://developers.cloudflare.com/cloudflare-one/access-controls/policies/mfa-requirements/).

Before Access and backend settings match, the gateway fails closed: no assertion returns 401, and missing backend Access configuration returns 503. It never falls back to local control or anonymous account data.

## 4. Match the backend JWT configuration

Copy the Access application's audience tag and your Zero Trust team domain into the existing local `.env`:

```dotenv
GARDIFUND_AUTH_MODE=cloudflare
CF_ACCESS_TEAM_DOMAIN=https://YOUR-TEAM.cloudflareaccess.com
CF_ACCESS_AUD=YOUR-ACCESS-APPLICATION-AUDIENCE-TAG
```

Use the audience for this Worker's production Access application. The team domain is not your Worker hostname. Preserve the Tradier key and local password hash. Restart the existing GARDiFUND task after saving; do not run a second instance against its data directory. Port 8766 independently checks the signed JWT, issuer, audience and expiry.

## 5. Link Pages and verify real synchronization

Set the GitHub repository variable `GARDIFUND_DASHBOARD_URL` to this Worker's HTTPS address and rerun the Pages workflow, as described in [the Pages guide](github-pages.md).

Open the Pages URL from a phone on cellular data. Follow its dashboard button, complete Access sign-in and MFA, and confirm the WAN app shows exactly **Overview, Charts, Back Test**. Verify both Live and Shadow Overview views load and show source timestamps. Open the local app and verify a refreshed local account/portfolio snapshot appears remotely through SSE. A `/api/state` request to the Worker must return 403, even after login. The remote app must provide no investing, settings, database, reset or order controls.

Stop the Windows app or Tunnel and confirm the remote connection stops updating; refreshing the Worker should return an unavailable response. Start them again and verify reconnection refreshes the view. The public Pages launch page remains available throughout. A URL being published, synthetic tests passing, or `/healthz` responding does not prove this real external connection and MFA flow.

The PC must remain awake, signed in, and running GARDiFUND plus its Tunnel. Sync does not run from GitHub Pages after the PC stops. The gateway forwards streams without buffering, stores no account snapshots, and disables response caching.
