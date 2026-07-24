#!/usr/bin/env bash
# QuantX Production V2 — Let's Encrypt renewal. Certbot no-ops if the
# current cert has >30 days left, so this is safe to run twice a day
# (quantx-v2-renew.timer) without hammering Let's Encrypt's rate limits.
set -Eeuo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

docker compose run --rm certbot renew --webroot -w /var/www/certbot --quiet
docker compose exec nginx nginx -s reload
