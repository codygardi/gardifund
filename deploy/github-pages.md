# GitHub Pages and the shared dashboard

[GARDiFUND on GitHub](https://github.com/codygardi/gardifund) · [Public launch page](https://codygardi.github.io/gardifund/)

GitHub Pages serves the public entry point. The **Open the secure dashboard** link opens the Cloudflare Worker, where viewers sign in with Access MFA. The Worker routes only the read-only UI and its data stream through a fixed VPC Service to the Windows app. Live account and market data stay in the local SQLite and Parquet stores and are read as viewers request them. Pages publishes only the five static launch files.

```text
GitHub Pages → Access-protected Worker → Tunnel VPC Service → 127.0.0.1:8766
Windows workspace and control API                            127.0.0.1:8765
```

The shared dashboard has Overview, Charts, and the Back Test placeholder. Overview shares aggregate account metrics by default. To share position and order details with approved viewers, set `GARDIFUND_SHARE_POSITION_DETAILS=true` in the local `.env` and restart. Account IDs, credentials, raw databases, and private notes stay local in both modes.

## Prepare the connection

1. Remove the old GARDiFUND private IP route and its port 80 Access application if they still exist. In Cloudflare One, delete only the old GARDiFUND `/32` under **Networks > Routes** and the matching application under **Access controls > Applications**. Keep the connector if other apps use it or if it will serve the new Worker.
2. In Cloudflare One, connect a named Tunnel to this Windows PC. The Windows connector must be running and use QUIC over outbound UDP 7844. A VPC Service is scoped to the viewing port, so this setup needs neither a public Tunnel hostname nor a private IP route. Keep Windows port 8765 reserved for local control. [Tunnel setup and VPC requirements](https://developers.cloudflare.com/workers-vpc/get-started/) · [VPC troubleshooting](https://developers.cloudflare.com/workers-vpc/reference/troubleshooting/)
3. In PowerShell, sign in to Wrangler with an account that can create VPC Services. Set the ID of the Tunnel connected to this PC and create a fixed HTTP service. Use `127.0.0.1` and `8766` as shown.

   ```powershell
   npx wrangler login
   npx wrangler vpc service create gardifund-view --type http --tunnel-id YOUR-TUNNEL-UUID --ipv4 127.0.0.1 --http-port 8766
   ```

   Save the returned **service ID**, which is distinct from the Tunnel ID. Keep the connector token out of Git and build variables. [VPC Services documentation](https://developers.cloudflare.com/workers-vpc/configuration/vpc-services/)

## Connect the Worker to GitHub

Create the `gardifund` Worker from `codygardi/gardifund` in Cloudflare Workers Builds. Configure these values before deploying.

| Setting | Value |
| --- | --- |
| Production branch | `main` |
| Root directory | Repository root |
| Build command | `node scripts/build-worker-config.mjs` |
| Deploy command | `npx wrangler deploy --config deploy/worker/wrangler.generated.json` |
| Build variable | `GARDIFUND_VPC_SERVICE_ID` = the VPC Service UUID |

The generator emits the fixed binding for the Worker in `deploy/worker/worker.js`. The deploy command runs that gateway. Uploading `dist/` as static assets would omit live data and offline handling. [Workers Builds configuration](https://developers.cloudflare.com/workers/ci-cd/builds/configuration/)

In **Workers & Pages > gardifund > Access**, choose **Protect this Worker behind Access** and **All traffic**. Allow only the intended viewer identities and require MFA in the Access application. The `workers.dev` hostname works without a purchased domain. Keep previews disabled. Copy the team's Access URL and the **production Worker application's** audience tag into the existing local `.env`.

```dotenv
GARDIFUND_AUTH_MODE=cloudflare
CF_ACCESS_TEAM_DOMAIN=https://YOUR-TEAM.cloudflareaccess.com
CF_ACCESS_AUD=YOUR-WORKER-ACCESS-APPLICATION-AUDIENCE-TAG
```

Preserve `PRODUCTION_API_KEY` and the local password hash. Restart the Windows task to apply the change. The viewing listener independently checks the Access JWT signature, issuer, audience, and expiry. [Access for Workers](https://developers.cloudflare.com/workers/configuration/cloudflare-access/) · [Access MFA requirements](https://developers.cloudflare.com/cloudflare-one/access-controls/policies/mfa-requirements/)

```powershell
Stop-ScheduledTask -TaskName GARDiFUND
Start-ScheduledTask -TaskName GARDiFUND
```

## Link and verify

1. Under the GitHub repository's **Settings > Secrets and variables > Actions > Variables**, set `GARDIFUND_DASHBOARD_URL` to the Worker's public HTTPS root, such as `https://gardifund.YOUR-SUBDOMAIN.workers.dev/`. This variable is a public URL. Pages uses **GitHub Actions** as its publishing source. Run **Actions > Verify and publish > Run workflow** after changing the URL. [Pages publishing source](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site)
2. Run `.\scripts\check-connections.ps1 -RemoteUrl https://gardifund.YOUR-SUBDOMAIN.workers.dev/`. The local viewer must return 401 without a JWT. The Worker must redirect anonymous requests to sign-in or deny them.
3. Open Pages from another network, follow the dashboard link, and complete MFA. Verify Overview, Charts and Back Test are present. Queue **Sync account** in the local Overview and verify that the signed-in dashboard refreshes over SSE. Check that `/api/state` on the Worker is denied even after sign-in.
4. Stop the Windows task during a maintenance window. A fresh signed-in dashboard request should show **Dashboard offline** and direct viewers to contact the admin to start the server. Restart the task and verify the dashboard reconnects. The public launch page remains available.

Workers VPC is free during its current beta. Check the plan and published limits before enabling paid resources. [Workers VPC availability](https://developers.cloudflare.com/workers-vpc/get-started/) · [Workers limits](https://developers.cloudflare.com/workers/platform/limits/)
