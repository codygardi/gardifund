# Public dashboard on GitHub Pages

Visitors open [GARDiFUND](https://codygardi.github.io/gardifund/) in their browser. The page displays Overview, Charts, and the Back Test placeholder. Overview shares aggregate Live and Shadow metrics. The page asks visitors to contact the admin when the Windows app is offline, and reconnects when it becomes available.

The Windows app serves the full workspace on `127.0.0.1:8765` and public read-only views on `127.0.0.1:8766`. The Worker has a fixed private Tunnel connection to port 8766. Both the Worker and the viewing listener deny controls and writes. The Pages artifact contains static UI assets. It fetches the public projections and SSE updates at runtime.

## 1. Connect the viewer

In the local `.env`, set `GARDIFUND_AUTH_MODE=public`. Start or restart the GARDiFUND task. In the [Workers VPC dashboard](https://dash.cloudflare.com/?to=/:account/workers/vpc), select **VPC Services > Create** and use these settings.

| Field | Value |
| --- | --- |
| Service name | `gardifund-view` |
| Tunnel | The healthy Tunnel connected to this Windows PC |
| Host or IP address | `127.0.0.1` |
| Ports | Custom HTTP port `8766` |

Copy the resulting **Service ID**. Use the dashboard's Windows connector instructions if the Tunnel needs to be connected. [Cloudflare VPC setup](https://developers.cloudflare.com/workers-vpc/get-started/)

## 2. Deploy the existing Worker

Run this in PowerShell from the workspace, and sign in to Cloudflare if Wrangler prompts.

```powershell
$env:GARDIFUND_VPC_SERVICE_ID = Read-Host 'Paste the VPC Service ID'
node .\scripts\build-worker-config.mjs
if ($LASTEXITCODE -eq 0) {
    npx.cmd wrangler deploy --config .\deploy\worker\wrangler.generated.json
}
```

The deploy command updates `gardifund` and binds `GARDIFUND_ORIGIN` to that service. In **Workers & Pages > gardifund > Access**, turn off the old production sign-in rule for this Worker after deploying the new gateway. Use the Worker-specific public option if an account-level rule applies. [Cloudflare Access controls](https://developers.cloudflare.com/workers/configuration/cloudflare-access/#disable-access)

## 3. Open the dashboard

The repository's Pages workflow publishes the dashboard with `GARDIFUND_DASHBOARD_URL` set to the HTTPS root of the Worker. The current value is `https://gardifund.codygardi22.workers.dev/`. Open the [public dashboard](https://codygardi.github.io/gardifund/) and check that Overview and Charts load. Use **Sync account** in the [local workspace](http://127.0.0.1:8765/) to verify the public view receives updates.

```powershell
.\scripts\check-connections.ps1 -RemoteUrl https://gardifund.codygardi22.workers.dev/
```

The script checks that the public state endpoint returns JSON with the Pages origin permitted and that the Worker denies the local API. To check outage behavior, stop the app during a maintenance window, reload the public page, and start the task again to observe recovery.

Workers VPC is free during its current beta. [Cloudflare VPC pricing](https://developers.cloudflare.com/workers-vpc/platform/pricing/)
