# QuantX AI Terminal

A single-user AI-assisted futures paper-trading dashboard: FastAPI backend (market data, quant signals, paper trading, bot control) + React/Vite frontend.

## Setup

### 1. Configure secrets

Copy the example env file and fill in real values — `.env` is git-ignored and never committed:

```bash
cp .env.example .env
```

Set these in `.env`:

- `DATABASE_URL` / `REDIS_URL` — connection strings for Postgres/Redis (defaults match the `docker-compose.yml` services).
- `SECRET_KEY` — random signing secret for login sessions. Generate one with:
  ```bash
  python3 -c "import secrets; print(secrets.token_urlsafe(48))"
  ```
- `ADMIN_USERNAME` — the login username.
- `ADMIN_PASSWORD_HASH` — a bcrypt hash of your login password (never store the plaintext password). Generate one with:
  ```bash
  python3 -c "import bcrypt; print(bcrypt.hashpw(b'your-password', bcrypt.gensalt()).decode())"
  ```

The frontend also has its own example file:

```bash
cp frontend/.env.example frontend/.env
```

### 2. Run the backend + data services

```bash
docker compose up -d
```

This starts `backend` (FastAPI, port `9000`), `postgres`, and `redis`. Docker Compose reads `backend`'s environment from the local `.env` via `env_file` in `docker-compose.yml` — no changes needed there.

Check it's up:

```bash
curl http://localhost:9000/api/health
```

### 3. Run the frontend

```bash
cd frontend
npm install
npm run dev
```

Log in with the `ADMIN_USERNAME` / password you set in step 1.

## Notes

- All API routes except `/api/health`, `/`, and `/api/auth/login` require a bearer token obtained via `/api/auth/login`.
- Paper trading state persists to `backend/data/paper.db` (SQLite), which is bind-mounted into the container.
