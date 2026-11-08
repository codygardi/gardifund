# First-time remote access setup

Use a named Cloudflare Tunnel to this Windows PC. GARDiFUND continues to run locally; Cloudflare provides the public address and Access login with MFA. GitHub Pages and a Workers deployment are not part of this setup.

```text
Phone or remote browser
  -> https://fund.YOUR-DOMAIN
  -> Cloudflare Access login + MFA
  -> Cloudflare Tunnel
  -> http://127.0.0.1:8766 on this Windows PC (read-only)
```

The local workspace is `http://127.0.0.1:8765`. Never select port **8765** as a tunnel destination. Keep the PC awake and signed in, with GARDiFUND running. The existing startup task starts at Windows sign-in, not before sign-in. The browser may be closed.

## 1. Obtain a domain

For a stable published hostname, Cloudflare requires a domain on your account. If you do not own one, create a Cloudflare account and use **Register domains** in its dashboard. Search for your preferred name, review the registration and renewal charges, register it yourself, and complete the registrant email verification. Choose a subdomain such as `fund` for GARDiFUND. `fund.example.com` below is an example, not an address that has been registered for you.

You can instead register through another registrar and add the domain's DNS to Cloudflare. No separate website-hosting purchase is needed for this Windows-hosted application. See [domain registration](https://developers.cloudflare.com/registrar/get-started/register-domain/) and [Tunnel prerequisites](https://developers.cloudflare.com/tunnel/get-started/).

## 2. Protect the intended hostname first

Open **Zero Trust** in the Cloudflare dashboard, finish its initial setup, and choose your team name. This creates a team domain such as `YOUR-TEAM.cloudflareaccess.com`; it is distinct from your purchased domain. You can find it later under **Zero Trust > Settings** ([team domain explanation](https://developers.cloudflare.com/cloudflare-one/faq/getting-started-faq/)).

Under **Access controls > Applications**, create a **self-hosted** application named GARDiFUND for your exact hostname, for example `fund.example.com`. Protect the entire hostname, leaving the path unrestricted. Configure a login method and an Allow policy for only your intended email addresses. Create this Access application before publishing the tunnel route ([self-hosted application setup](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/self-hosted-public-app/)).

Enable independent MFA in **Access controls > Access settings**. Allow an authenticator app or a security key. Then edit GARDiFUND under **Applications > Authentication > MFA**, require MFA for this application, and choose **Require every login**. Check that its Allow policy does not override MFA with an exemption. Cloudflare account MFA protects the dashboard; the application's Access MFA protects GARDiFUND. See [independent MFA](https://developers.cloudflare.com/cloudflare-one/access-controls/access-settings/independent-mfa/) and [application MFA requirements](https://developers.cloudflare.com/cloudflare-one/access-controls/policies/mfa-requirements/).

## 3. Configure GARDiFUND's token validation

Copy the application's **Application Audience (AUD) Tag** from its Access details. Edit the existing local `.env` file in the workspace; replace only these settings with the matching real values:

```dotenv
GARDIFUND_AUTH_MODE=cloudflare
CF_ACCESS_TEAM_DOMAIN=https://YOUR-TEAM.cloudflareaccess.com
CF_ACCESS_AUD=YOUR-ACCESS-APPLICATION-AUDIENCE-TAG
```

The team domain is not `fund.example.com`, and the audience is not the tunnel ID. Preserve your existing Tradier key and password hash. Keep API keys, tunnel tokens and credential files out of chat, source files and Git.

Restart the existing **GARDiFUND** task in Task Scheduler after saving. Stop it, wait for it to stop, then start it again. Do not launch a second copy against the same data directory. The backend validates Access signatures, issuer, audience and expiry independently of Cloudflare's policy.

## 4. Connect the Windows PC

In the main Cloudflare dashboard, open **Networking > Tunnels**, create a tunnel named `gardifund`, and select the Windows connector instructions. This workspace already has a verified Windows executable at:

```text
F:\Projects\Finance\GardiFund\.tools\cloudflared.exe
```

Follow the dashboard's **Install and Run** instructions on this PC. Installing its Windows service requires an administrator terminal; use the executable above if `cloudflared` is not on your PATH. The installation command contains a tunnel token: run it locally and do not paste that command into chat or commit it. A healthy connector should appear in the dashboard. This dashboard-managed approach does not use the locally-managed YAML example in this folder.

Add the published application route under the tunnel's **Routes** tab:

| Setting | Value |
| --- | --- |
| Route type | Published application |
| Hostname | Your exact Access-protected hostname, e.g. `fund.example.com` |
| Service type | HTTP |
| Service address | `127.0.0.1:8766` |
| Full service URL | `http://127.0.0.1:8766` |

Enable **Protect with Access** in the route's additional settings and select the same Access application/team/audience. The backend still checks every protected request. Save the route. No router port-forward is required. See [Tunnel setup](https://developers.cloudflare.com/tunnel/get-started/) and [origin Access validation](https://developers.cloudflare.com/tunnel/reference/origin-parameters/#access).

## 5. Verify the real connection

From the workspace, use your actual hostname:

```powershell
.\scripts\check-connections.ps1 -RemoteUrl https://fund.example.com
```

Then open the hostname in a private browser window on a phone using cellular data. Confirm Access blocks anonymous access, your permitted identity must complete MFA, and Overview and Charts load after login. Investing, News, Database, Settings and trade/reset controls must be unavailable. In the authenticated browser, `/api/state` must return 403. Synthetic JWT tests and a basic `/healthz` response do not replace this real MFA and external-network check.

If Cloudflare returns 502, check that this PC is awake, the connector is healthy, and GARDiFUND is running on the exact service port. A 503 from the read-only origin means Access configuration or signing-key availability is not ready; a 401 after login calls for checking the matching team domain and audience. Never change the destination to port 8765 to work around a login failure.

Remote access is complete only after these checks pass. Until then, the local workspace remains usable at `http://127.0.0.1:8765`.
