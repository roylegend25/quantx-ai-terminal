# QuantX Terminal domain setup

The VM public IPv4 is currently `34.88.253.252`. Verify it in Google Cloud
before adding DNS; reserve a static external IP so it cannot change.

## DNS records

| Type | Name | Target | TTL | Cloudflare initially |
|---|---|---|---|---|
| A | `@` | `34.88.253.252` | Auto | DNS only (grey cloud) |
| CNAME | `www` | `quantxterminal.com` | Auto | DNS only (grey cloud) |
| A | `api` | `34.88.253.252` | Auto | DNS only (grey cloud) |

The `api` record is optional because the frontend uses same-origin
`https://quantxterminal.com/api/...`. Keep it for the configured API hostname.
If CNAME is unavailable for `www`, use an A record to `34.88.253.252`. Do not
add AAAA unless the VM has working public IPv6. Remove stale conflicting records.

## Install the prepared HTTP Nginx config

```bash
cd ~/quantx-ai-terminal
sudo cp /etc/nginx/sites-available/quantx \
  /etc/nginx/sites-available/quantx.pre-domain.$(date -u +%Y%m%dT%H%M%SZ)
sudo cp nginx/default.conf /etc/nginx/sites-available/quantx
sudo ln -sfn /etc/nginx/sites-available/quantx /etc/nginx/sites-enabled/quantx
sudo nginx -t
sudo systemctl reload nginx
```

Verify all names return `34.88.253.252`:

```bash
dig +short A quantxterminal.com
dig +short A www.quantxterminal.com
dig +short A api.quantxterminal.com
```

Allow inbound TCP 80 and 443 in the Google Cloud firewall. DNS propagation can
take up to the previous record's TTL.

## Enable HTTPS only after DNS resolves

```bash
sudo apt-get update
sudo apt-get install -y certbot python3-certbot-nginx
sudo certbot --nginx \
  -d quantxterminal.com \
  -d www.quantxterminal.com \
  -d api.quantxterminal.com \
  --redirect
sudo nginx -t
sudo systemctl reload nginx
sudo certbot renew --dry-run
systemctl status certbot.timer --no-pager
```

After HTTPS succeeds, uncomment HSTS in the root server and change the `www`
redirect from `http://` to `https://`, then test and reload. Do not enable HSTS
before HTTPS works. If omitting the API hostname, also omit its Certbot `-d`.

## Cloudflare

Keep records **DNS only** for initial issuance. After origin HTTPS works,
proxying may be enabled with SSL/TLS mode **Full (strict)**—never Flexible.
Enable "Always Use HTTPS" only after the origin redirect is verified. When
proxied, configure Cloudflare real visitor IPs in Nginx before relying on
per-client rate limits.

## Build and smoke-test

```bash
cd ~/quantx-ai-terminal/frontend
npm ci
npm run build

curl -I -H 'Host: quantxterminal.com' http://127.0.0.1/
curl -I -H 'Host: www.quantxterminal.com' http://127.0.0.1/
curl -H 'Host: quantxterminal.com' http://127.0.0.1/api/health
curl -H 'Host: api.quantxterminal.com' http://127.0.0.1/api/health
```

Local development works because Vite proxies `/api` and `/ws` to port 9000.
For a Vercel or Cloudflare Pages frontend fallback, set
`VITE_API_URL=https://api.quantxterminal.com`; that API must have HTTPS and an
appropriate CORS policy.

- Vercel project `quantxterminal` normally yields `quantxterminal.vercel.app`
  if available.
- Cloudflare Pages project `quantx-terminal` normally yields
  `quantx-terminal.pages.dev` if available.

Use `frontend` as project root, `npm run build` as build command, and `dist` as
output. These platforms host only the static frontend, not the Docker backend.
