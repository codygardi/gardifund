# Public dashboard on GitHub Pages

Visitors open [GARDiFUND](https://codygardi.github.io/gardifund/) in their browser. The page displays Account and a Research placeholder. The local and public Account views show the same Live and Shadow balances, performance, and position and order counts. Individual positions and orders can be opened in the Windows workspace. The page asks visitors to contact the admin when the Windows app is offline, and reconnects when it becomes available.

The Windows app serves the full workspace on `127.0.0.1:8765` and public read-only views on `127.0.0.1:8766`. The Worker has a fixed private Tunnel connection to port 8766. Both the Worker and the viewing listener deny controls and writes. The Pages artifact contains static UI assets. It fetches the public projections and SSE updates at runtime.

After the one-time setup below, start the **GARDiFUND** task in Windows and open the [local workspace](http://127.0.0.1:8765/). Share the Pages link with viewers. The task also starts the connector if you stored the Tunnel token in `.tools/viewer-tunnel.token`.

## One-time setup on the Windows PC

Set `GARDIFUND_AUTH_MODE=public` in the local `.env` and start the GARDiFUND task. Run this from the workspace in PowerShell.

```powershell
.\scripts\enable-sharing.ps1
```

Approve Wrangler in the Cloudflare account that owns the `gardifund` Worker. Enter the one-time code shown in PowerShell when prompted. The script creates or reuses the dedicated Tunnel and VPC Service, deploys the Worker, and saves the connector token locally. Restart the GARDiFUND task once after setup. [Cloudflare VPC setup](https://developers.cloudflare.com/workers-vpc/get-started/)

## Open the dashboard

Open the [public dashboard](https://codygardi.github.io/gardifund/) and check that Account and Research load. Visitors need only this link. Use **Sync account** in the [local workspace](http://127.0.0.1:8765/) to verify the public view receives updates.

```powershell
.\scripts\check-connections.ps1 -RemoteUrl https://gardifund.codygardi22.workers.dev/
```

The check verifies that the Worker returns data to Pages and denies the local API. If it redirects to a sign-in page, disable the sign-in rule for this Worker under **Workers & Pages > gardifund > Access**. To check outage behavior, stop the app during a maintenance window, reload the public page, and start the task again to observe recovery. [Cloudflare Access controls](https://developers.cloudflare.com/workers/configuration/cloudflare-access/#disable-access)

Workers VPC is free during its current beta. [Cloudflare VPC pricing](https://developers.cloudflare.com/workers-vpc/platform/pricing/)
