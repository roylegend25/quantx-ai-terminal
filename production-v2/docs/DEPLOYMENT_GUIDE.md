# Deployment Guide (Phase 3)

Two halves: steps 1–2 and 6 run **from your workstation** (the VM doesn't exist yet, or its
firewall is a project-level GCP resource, not something inside the VM). Steps 3–17 run **on the
VM** via `deploy.sh`, which you trigger once by SSH-ing in. Fill in `PROJECT`, `ZONE`, `VM_NAME`
before running anything below.

```bash
export PROJECT=your-gcp-project-id
export ZONE=us-central1-a
export VM_NAME=quantx-prod-v2
```

## 1. Create the new Google Cloud VM

```bash
gcloud compute instances create "$VM_NAME" \
  --project="$PROJECT" \
  --zone="$ZONE" \
  --machine-type=e2-standard-2 \
  --image-family=ubuntu-2404-lts-amd64 \
  --image-project=ubuntu-os-cloud \
  --boot-disk-size=50GB \
  --boot-disk-type=pd-ssd \
  --tags=quantx-prod-v2 \
  --network-interface=network-tier=PREMIUM,subnet=default
```
See `docs/OPERATIONS.md` "VM sizing" for why `e2-standard-2` / 50GB is the recommended starting
point, and how to size up.

## 2. Install Ubuntu

Already done by `--image-family=ubuntu-2404-lts-amd64` above — GCP images boot straight to a
running Ubuntu 24.04 LTS, there is no separate OS-install step for a Compute Engine VM.

## 3–5. Docker, Docker Compose, Git

Handled by `deploy.sh` (idempotent — skips anything already installed). Not run yet; happens
automatically once you invoke the script in step 8.

## 6. Configure firewall

This is a project-level GCP resource, not something inside the VM — create it once per project
(re-running for a second VM with the same tag is a no-op):

```bash
gcloud compute firewall-rules create quantx-prod-v2-web \
  --project="$PROJECT" \
  --network=default \
  --direction=INGRESS \
  --action=ALLOW \
  --rules=tcp:80,tcp:443 \
  --target-tags=quantx-prod-v2 \
  --source-ranges=0.0.0.0/0

gcloud compute firewall-rules create quantx-prod-v2-ssh \
  --project="$PROJECT" \
  --network=default \
  --direction=INGRESS \
  --action=ALLOW \
  --rules=tcp:22 \
  --target-tags=quantx-prod-v2 \
  --source-ranges=<YOUR_OFFICE_OR_VPN_CIDR>   # narrow this — do not leave 0.0.0.0/0 on SSH
```
`deploy.sh` additionally configures `ufw` inside the VM as a second layer (see step 6 in the
script) — the GCP rule above is the one that actually matters for what reaches the VM at all.

Get the VM's public IP for your DNS record:
```bash
gcloud compute instances describe "$VM_NAME" --zone="$ZONE" \
  --format='get(networkInterfaces[0].accessConfigs[0].natIP)'
```
Point your V2 domain's `A` record at that IP now — Let's Encrypt (step 7) needs it resolvable
before it can issue a certificate.

## 7. Configure HTTPS

Handled by `deploy.sh` (dummy-cert bootstrap → Let's Encrypt via certbot, see the script's
comments) once DNS above has propagated. Renewal is automatic via `quantx-v2-renew.timer`
(installed by the same script) — see `docs/OPERATIONS.md`.

## 8. Clone GitHub → 17. Configure logs

SSH into the VM and run the bootstrap script — this single command performs steps 3–5, 8–17:

```bash
gcloud compute ssh "$VM_NAME" --zone="$ZONE" --project="$PROJECT"

# on the VM:
curl -fsSL https://raw.githubusercontent.com/roylegend25/quantx-ai-terminal/production-v2/production-v2/deploy.sh -o deploy.sh
chmod +x deploy.sh
./deploy.sh
```
The first run stops after creating `.env` from `.env.example` and tells you exactly which
`CHANGE-ME` values remain (`grep CHANGE-ME .env`). Fill those in (see `docs/OPERATIONS.md`
"Required secrets"), then run `./deploy.sh` again — it resumes from where it stopped and completes
steps 9–17 (clone/pull already done, so it goes straight to build → HTTPS → systemd → backups).

What each remaining step maps to in the script, if you want to follow along:

| Phase 3 step | Where in `deploy.sh` |
|---|---|
| 9. Configure `.env` | ".env" section — creates from example, fails closed on any `CHANGE-ME` |
| 10. Build containers | `docker compose build` |
| 11. Configure Nginx | HTTPS bootstrap section (dummy cert → real cert → `docker compose up -d nginx`) |
| 12. Configure systemd | "Installing systemd units" — `quantx-v2.service` (boot ordering), timers |
| 13. Configure backups | Installs and enables `quantx-v2-backup.timer` (daily, see `docs/BACKUP_GUIDE.md`) |
| 14. Configure restart policies | Already declared in `docker-compose.yml` (`restart: unless-stopped` per service) |
| 15. Configure monitoring | Compose healthchecks + `/api/health/*` (see `docs/OPERATIONS.md` "Health check commands") |
| 16. Configure logs | `json-file` driver, 50MB × 5 files per container, set in `docker-compose.yml` |
| 17. Configure update process | Not part of first boot — see `docs/UPDATE_GUIDE.md` |
| 18. Configure rollback process | Not part of first boot — see `docs/ROLLBACK_GUIDE.md` |

## 18. Verify

```bash
curl -fsS https://<your-domain>/api/health/ready
curl -fsS https://<your-domain>/api/health/status
```
Both should return `200`. Then open `https://<your-domain>/` and confirm the 8 pages load
(Dashboard, Predictions, Paper Trading, Binance, Daily Accuracy, Bot Settings, System Health, and
Decision Engine reasoning showing on the Dashboard) and that `https://<your-domain>/api/research/`
returns `404` (edge block working, see `production-v2/nginx/nginx.conf`).
