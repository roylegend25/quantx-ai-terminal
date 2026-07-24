#!/usr/bin/env bash
# QuantX Production V2 — first-boot bootstrap.
#
# Run this ON a fresh Ubuntu 22.04/24.04 GCP VM after you've SSH'd in (see
# docs/DEPLOYMENT_GUIDE.md steps 1-2 for creating the VM itself — that part
# runs from your workstation, before the VM exists, so it can't live here).
#
# From a clean VM this script alone takes you to a running stack using only
# this file + docker-compose.yml + .env.example (+ the repo it clones). The
# only manual step is filling in secrets in .env — everything else is
# unattended and safe to re-run (idempotent): re-running after a partial
# failure resumes rather than duplicating work.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/roylegend25/quantx-ai-terminal/production-v2/production-v2/deploy.sh -o deploy.sh
#   chmod +x deploy.sh
#   ./deploy.sh
set -Eeuo pipefail

# ---------------------------------------------------------------------------
# Configuration (override by exporting before running, or editing here)
# ---------------------------------------------------------------------------
REPO_URL="${REPO_URL:-git@github.com:roylegend25/quantx-ai-terminal.git}"
DEPLOY_BRANCH="${DEPLOY_BRANCH:-production-v2}"
INSTALL_DIR="${INSTALL_DIR:-/opt/quantx}"
DATA_DIR="${DATA_DIR:-$INSTALL_DIR/data}"
BACKUP_DIR="${BACKUP_DIR:-$INSTALL_DIR/backups}"

log()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m!! %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31mFATAL: %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" -ne 0 ] || die "Run as a normal sudo-capable user, not root directly (Docker group setup below assumes this)."
command -v sudo >/dev/null || die "sudo is required."

# ---------------------------------------------------------------------------
# Step 3: Install Docker
# ---------------------------------------------------------------------------
if ! command -v docker >/dev/null; then
  log "Installing Docker Engine + Compose plugin"
  sudo apt-get update -y
  sudo apt-get install -y ca-certificates curl gnupg
  sudo install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  sudo chmod a+r /etc/apt/keyrings/docker.gpg
  . /etc/os-release
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
    | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
  sudo apt-get update -y
  sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  sudo usermod -aG docker "$USER"
  warn "Added $USER to the docker group. If this is the first install, log out/in (or run 'newgrp docker') before re-running this script."
else
  log "Docker already installed ($(docker --version))"
fi
command -v docker >/dev/null || die "Docker install failed."
docker compose version >/dev/null 2>&1 || die "Docker Compose plugin missing."

# ---------------------------------------------------------------------------
# Step 5: Install Git
# ---------------------------------------------------------------------------
if ! command -v git >/dev/null; then
  log "Installing Git"
  sudo apt-get install -y git
fi

# ---------------------------------------------------------------------------
# Step 6: Configure firewall (defense in depth — the primary firewall is the
# GCP VPC firewall rule created from your workstation, see
# docs/DEPLOYMENT_GUIDE.md step 6; this is the host-level backstop)
# ---------------------------------------------------------------------------
if command -v ufw >/dev/null; then
  log "Configuring ufw (22/tcp, 80/tcp, 443/tcp only)"
  sudo ufw allow OpenSSH >/dev/null
  sudo ufw allow 80/tcp >/dev/null
  sudo ufw allow 443/tcp >/dev/null
  sudo ufw --force enable
else
  warn "ufw not found — skipping host firewall (rely on the GCP VPC firewall rule)."
fi

# ---------------------------------------------------------------------------
# Step 8: Clone GitHub (the deploy branch only — see docs/GITHUB_WORKFLOW.md)
# ---------------------------------------------------------------------------
if [ -d "$INSTALL_DIR/.git" ]; then
  log "Repo already present at $INSTALL_DIR — fetching latest $DEPLOY_BRANCH"
  git -C "$INSTALL_DIR" fetch origin "$DEPLOY_BRANCH"
  git -C "$INSTALL_DIR" checkout "$DEPLOY_BRANCH"
  git -C "$INSTALL_DIR" reset --hard "origin/$DEPLOY_BRANCH"
else
  log "Cloning $REPO_URL ($DEPLOY_BRANCH) into $INSTALL_DIR"
  sudo mkdir -p "$INSTALL_DIR"
  sudo chown "$USER":"$USER" "$INSTALL_DIR"
  git clone --branch "$DEPLOY_BRANCH" --single-branch "$REPO_URL" "$INSTALL_DIR"
fi
cd "$INSTALL_DIR/production-v2"

# ---------------------------------------------------------------------------
# Step 9: Configure .env — the only step that needs a human
# ---------------------------------------------------------------------------
if [ ! -f .env ]; then
  log "No .env found — creating from .env.example. Fill in every CHANGE-ME value, then re-run this script."
  cp .env.example .env
  chmod 600 .env
  die ".env created at $INSTALL_DIR/production-v2/.env — edit it (secrets, DOMAIN, REPO_URL) and re-run ./deploy.sh"
fi
if grep -q "CHANGE-ME" .env; then
  die "$(grep -c CHANGE-ME .env) placeholder(s) still in .env (grep CHANGE-ME .env to list them) — fill them in and re-run."
fi
set -a; source .env; set +a
[ -n "${DOMAIN:-}" ] || die "DOMAIN is not set in .env"

mkdir -p "$DATA_DIR" "$BACKUP_DIR"
export DATA_DIR BACKUP_DIR
export APP_GIT_SHA; APP_GIT_SHA="$(git -C "$INSTALL_DIR" rev-parse --short=12 HEAD)"
export APP_IMAGE_TAG="v2-${APP_GIT_SHA}"
sed -i "s/^APP_GIT_SHA=.*/APP_GIT_SHA=${APP_GIT_SHA}/" .env
sed -i "s/^APP_IMAGE_TAG=.*/APP_IMAGE_TAG=${APP_IMAGE_TAG}/" .env

# ---------------------------------------------------------------------------
# Step 10: Build containers
# ---------------------------------------------------------------------------
log "Building images (backend + nginx) — first build downloads the full ML
toolchain the backend still imports at startup (see ARCHITECTURE.md 'Image
size'), this can take several minutes"
docker compose build

# ---------------------------------------------------------------------------
# Step 7 + 11: HTTPS bootstrap + Nginx
# Standard dummy-cert pattern (https://github.com/wmnnd/nginx-certbot):
# nginx cannot start with a `ssl_certificate` directive pointing at a file
# that doesn't exist yet, and certbot needs nginx running (for the HTTP-01
# challenge) to issue the first real certificate — so we create a throwaway
# self-signed cert first, start nginx, replace it with the real one, reload.
# ---------------------------------------------------------------------------
docker compose up -d redis
log "Waiting for redis..."
until docker compose exec -T redis redis-cli ping 2>/dev/null | grep -q PONG; do sleep 2; done

docker compose up -d backend
log "Waiting for backend health..."
for i in $(seq 1 60); do
  docker compose exec -T backend curl -fsS http://localhost:8000/api/health/live >/dev/null 2>&1 && break
  [ "$i" -eq 60 ] && die "Backend did not become healthy — check: docker compose logs backend"
  sleep 5
done

CERT_LIVE_DIR_CHECK="$(docker compose run --rm --entrypoint sh certbot -c "[ -f /etc/letsencrypt/live/${DOMAIN}/fullchain.pem ] && echo yes || echo no" 2>/dev/null | tail -1)"
if [ "$CERT_LIVE_DIR_CHECK" != "yes" ]; then
  log "No existing certificate for $DOMAIN — bootstrapping with a dummy self-signed cert"
  docker compose run --rm --entrypoint sh certbot -c "
    mkdir -p /etc/letsencrypt/live/${DOMAIN} &&
    openssl req -x509 -nodes -newkey rsa:2048 -days 1 \
      -keyout /etc/letsencrypt/live/${DOMAIN}/privkey.pem \
      -out /etc/letsencrypt/live/${DOMAIN}/fullchain.pem \
      -subj '/CN=${DOMAIN}'
  "
  docker compose up -d nginx
  log "Requesting real Let's Encrypt certificate for $DOMAIN"
  docker compose run --rm --entrypoint sh certbot -c "rm -rf /etc/letsencrypt/live/${DOMAIN} /etc/letsencrypt/archive/${DOMAIN} /etc/letsencrypt/renewal/${DOMAIN}.conf"
  docker compose run --rm certbot certonly --webroot -w /var/www/certbot \
    -d "$DOMAIN" --email "${LETSENCRYPT_EMAIL}" --agree-tos --non-interactive \
    || die "certbot failed — check DNS for $DOMAIN points at this VM's public IP, and that port 80 is reachable"
  docker compose exec nginx nginx -s reload
else
  log "Existing certificate found for $DOMAIN — starting nginx"
  docker compose up -d nginx
fi

# ---------------------------------------------------------------------------
# Flip maintenance mode off once every service reports healthy
# ---------------------------------------------------------------------------
log "Final health check before leaving maintenance mode"
for i in $(seq 1 30); do
  docker compose exec -T backend curl -fsS http://localhost:8000/api/health/ready >/dev/null 2>&1 && break
  [ "$i" -eq 30 ] && die "Backend never became ready — leaving DEPLOYMENT_MAINTENANCE_MODE=true. Check: docker compose logs backend"
  sleep 5
done
sed -i "s/^DEPLOYMENT_MAINTENANCE_MODE=.*/DEPLOYMENT_MAINTENANCE_MODE=false/" .env
docker compose up -d backend
# nginx already started above and resolved "backend"'s IP at its own startup
# via Docker's embedded DNS — recreating the backend container here gives it
# a new IP that nginx won't notice without a reload (upstream DNS is cached
# until reload, same reasoning as scripts/update.sh).
docker compose exec nginx nginx -s reload 2>/dev/null || true

# ---------------------------------------------------------------------------
# Steps 12-16: systemd, backups, restart policy, monitoring, logs
# ---------------------------------------------------------------------------
log "Installing systemd units (boot ordering + backup timer)"
sudo cp systemd/quantx-v2.service /etc/systemd/system/
sudo cp systemd/quantx-v2-backup.service /etc/systemd/system/
sudo cp systemd/quantx-v2-backup.timer /etc/systemd/system/
sudo cp systemd/quantx-v2-renew.service /etc/systemd/system/
sudo cp systemd/quantx-v2-renew.timer /etc/systemd/system/
sudo sed -i "s#__INSTALL_DIR__#$INSTALL_DIR#g" /etc/systemd/system/quantx-v2.service /etc/systemd/system/quantx-v2-backup.service /etc/systemd/system/quantx-v2-renew.service
sudo sed -i "s#__BACKUP_DIR__#$BACKUP_DIR#g" /etc/systemd/system/quantx-v2-backup.service
chmod +x scripts/backup.sh scripts/renew-cert.sh
sudo systemctl daemon-reload
sudo systemctl enable --now quantx-v2.service
sudo systemctl enable --now quantx-v2-backup.timer
sudo systemctl enable --now quantx-v2-renew.timer

log "Logging: json-file driver, 50MB x 5 files per container (already set in docker-compose.yml) — see docs/OPERATIONS.md for log commands."

log "Deployment complete."
docker compose ps
cat <<EOF

Next:
  - Verify:        curl -fsS https://${DOMAIN}/api/health/ready
  - Dashboard:     https://${DOMAIN}/
  - Logs:          docker compose -f $INSTALL_DIR/production-v2/docker-compose.yml logs -f backend
  - Backups:       systemctl status quantx-v2-backup.timer
  - Cert renewal:  systemctl status quantx-v2-renew.timer (see docs/OPERATIONS.md)

Trading is running in PAPER mode (TRADING_MODE=paper, BINANCE_LIVE_ENABLED=false
in .env). Do not flip to live trading until you've read docs/OPERATIONS.md
"Going live" and completed its checklist.
EOF
