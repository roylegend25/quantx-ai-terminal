#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$PROJECT_DIR/frontend"
npm install
npm run build
test -f dist/index.html

cd "$PROJECT_DIR"
docker compose up -d --build backend
sudo nginx -t
sudo systemctl reload nginx

curl --fail --silent --show-error http://127.0.0.1:9000/api/health >/dev/null
curl --fail --silent --show-error https://www.quantxterminal.com/api/health >/dev/null

printf '\nProduction URL:\nhttps://www.quantxterminal.com\n'
