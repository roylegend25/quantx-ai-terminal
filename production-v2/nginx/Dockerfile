# Multi-stage build: compile the frontend, then serve it from a plain nginx
# image alongside the reverse-proxy config. Build context is the repo root
# (see docker-compose.yml), so frontend/ and production-v2/ are both visible.

FROM node:22-alpine AS build
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
# frontend/.env.production already sets VITE_API_BASE_URL=/api (same-origin,
# proxied by nginx below) — no build-time secrets required.
RUN npm run build

FROM nginx:1.27-alpine
RUN apk add --no-cache curl
# Templated, not a static copy: nginx's own docker-entrypoint.d/20-envsubst-
# on-templates.sh runs `envsubst` over every *.template file here at
# container start, substituting only real environment variables (DOMAIN) —
# nginx's own runtime variables ($host, $request_uri, etc.) are untouched
# because they are never set in the container's actual environment.
COPY production-v2/nginx/nginx.conf /etc/nginx/templates/default.conf.template
COPY --from=build /src/dist /usr/share/nginx/html
