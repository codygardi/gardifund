# Connect through your private Cloudflare Tunnel

The existing Windows Tunnel can serve GARDiFUND to devices enrolled in your Cloudflare Zero Trust organization. Open the private IPv4 address in the browser while Cloudflare One Client (WARP) is connected. This route uses the private Access application and your Tunnel. It requires no public hostname, Worker, or GitHub deployment. [Cloudflare private applications](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/non-http/self-hosted-private-app/)

| Where | Address | Workspace |
| --- | --- | --- |
| The Windows host | `http://127.0.0.1:8765/` | All seven modules |
| An enrolled viewer's device | `http://YOUR-PRIVATE-IP/` | Overview, Charts, Back Test |

## 1. Choose the Windows viewing address

Use the stable IPv4 address on the Windows PC's Ethernet or Wi-Fi adapter. PowerShell's `Get-NetIPConfiguration` shows the address. Prefer a DHCP reservation so the address stays fixed. Keep the `cloudflared` Windows service running on this same PC. The connector replica ID displayed in Cloudflare identifies the running connector. Select its parent Tunnel when you add the route.

## 2. Create the route and Access application

1. In Cloudflare One, go to **Networking > Routes** and add a CIDR route for just the selected address, for example `192.168.0.175/32`. Select your existing Tunnel and its virtual network. Use a private route. [Cloudflare IP routes](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/private-net/cloudflared/connect-cidr/)
2. In **Access controls > Applications**, create a **Self-hosted and private** application. Add the exact private IP and port **80**. Use the same virtual network if you select one. Restrict its Allow policy to the intended identities and require MFA. Use Cloudflare independent MFA for this application or a supported identity provider that reports MFA. [Private Access setup](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/non-http/self-hosted-private-app/) · [MFA settings](https://developers.cloudflare.com/cloudflare-one/access-controls/policies/mfa-requirements/)
3. Enable the Gateway TCP proxy in **Traffic policies > Traffic settings > Proxy and inspection**. Enroll each viewer's device in your organization using Cloudflare One Client. Ensure its Split Tunnel profile routes your exact `/32` through Cloudflare. An Include profile can limit routing to the app and the required Zero Trust destinations. The default exclusions may cover your private subnet. [Gateway proxy and device routing](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/private-net/cloudflared/connect-cidr/) · [Split Tunnel configuration](https://developers.cloudflare.com/cloudflare-one/team-and-resources/devices/cloudflare-one-client/configure/route-traffic/split-tunnels/)

Cloudflare supports browser sign-in and an origin application JWT for private HTTP apps on port 80. The Cloudflare One Client and Tunnel encrypt their respective connections to Cloudflare. The connector and GARDiFUND run on the same Windows PC. The app checks the real connection source and requires a valid Access JWT for shared data. Use the enrolled client to view the app and leave router port forwarding off. [Private HTTP authentication](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/non-http/self-hosted-private-app/#plaintext-http-applications)

## 3. Connect the Windows app

In the existing local `.env`, set the private IP, the full Zero Trust team URL with its `https://` prefix, and the Audience (AUD) Tag from the Access application's Additional settings. [Cloudflare JWT settings](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/validating-json/#get-your-aud-tag)

```dotenv
GARDIFUND_PRIVATE_VIEW_IP=192.168.0.175
CF_ACCESS_TEAM_DOMAIN=https://YOUR-TEAM.cloudflareaccess.com
CF_ACCESS_AUD=YOUR-PRIVATE-ACCESS-APPLICATION-AUDIENCE
```

Use the IP you routed. Keep your existing Tradier and local password settings in the file. The private viewer binds to port 80 after valid Access settings are present. GARDiFUND runs its local control listener at `127.0.0.1:8765`. Restart the `GARDiFUND` task after editing `.env`. Keep the PC awake with the GARDiFUND task and the `cloudflared` service running.

## 4. Verify the connection

On the Windows host, run the check from the project folder.

```powershell
Get-Service cloudflared
.\scripts\check-connections.ps1 -RequirePrivateView
```

The local endpoints should return 200. The private origin should return 401 without a JWT. If port 80 is busy or the chosen IP has changed, the local app continues running and the private check fails. Check the Windows adapter address and the process using port 80.

On an enrolled device on another network, connect Cloudflare One Client and open `http://YOUR-PRIVATE-IP/`. Complete Access sign-in and MFA. After authentication, `/scope` should return `{"access_scope":"remote"}`. The app should show **Overview, Charts, and Back Test**. Check that Overview and Charts load, and run an account refresh from the local app to see the Overview update over SSE. A request for `/api/state` on the private viewer must return 403 even after sign-in.

The private origin uses cached market and portfolio data from the running PC. It serves a read-only projection of that data. Back Test remains a placeholder.
