# Deploying S3FastBrowser

This page covers a production-style installation with Docker Compose behind a TLS
reverse proxy. For a quick local try, see the [README](../README.md#quick-start-docker).

## 1. Requirements

- Docker Engine with the Compose plugin (v2.24 or later, for optional `env_file`).
- A DNS name for the app (for example `s3.example.com`) and a reverse proxy that
  terminates TLS: Caddy, nginx, Traefik, HAProxy...
- Network access from the host to your S3 endpoints.

## 2. Install

```bash
git clone https://github.com/mmiretta/s3fastbrowser.git /opt/s3fastbrowser
cd /opt/s3fastbrowser
cp .env.example .env
chmod 600 .env
```

Edit `.env`. The minimum for a shared installation:

```ini
SECRET_KEY=<output of: python3 -c "import secrets; print(secrets.token_hex(32))">

# Login (pick one)
S3FB_AUTH_USERNAME=admin
S3FB_AUTH_PASSWORD_HASH='pbkdf2:sha256:...'   # see below; keep the single quotes

# Only allow your storage endpoints (prevents SSRF)
S3FB_ALLOWED_ENDPOINTS=https://s3.example.com,https://s3.*.amazonaws.com
S3FB_DEFAULT_ENDPOINT=https://s3.example.com

# Behind TLS
SESSION_COOKIE_SECURE=true
S3FB_TRUST_PROXY=true
```

Create the password hash with the image itself:

```bash
docker compose build
docker compose run --rm s3fastbrowser python src/auth.py hash-password
```

Then start it:

```bash
docker compose up -d
docker compose logs -f s3fastbrowser     # look for warnings at start-up
curl -fsS http://127.0.0.1:5001/healthz  # {"status": "ok", ...}
```

The container listens on `127.0.0.1:5001` on the host (`S3FB_BIND`, `S3FB_PORT`). If
the reverse proxy runs in another container, either attach both to a shared Docker
network and proxy to `s3fastbrowser:5001`, or set `S3FB_BIND` to the host's Docker
bridge gateway address (the IP of the `docker0` interface) and proxy to that. Do not publish the port on `0.0.0.0`.

## 3. Reverse proxy

The app needs nothing special from the proxy, but keep these in mind:

- **Uploads** go through the app: raise the body size limit (or disable it).
- **Downloads and ZIPs** are streamed: disable response buffering and allow long
  transfers (the image uses a 300 s worker timeout per idle period; adjust
  `GUNICORN_TIMEOUT` for very slow links).
- Forward `X-Forwarded-For` and `X-Forwarded-Proto`, and set `S3FB_TRUST_PROXY=true`
  so redirects, secure cookies and the login rate limiter see the real client.

### Caddy

```caddy
s3.example.com {
    encode gzip
    reverse_proxy 127.0.0.1:5001 {
        flush_interval -1          # stream downloads and ZIPs immediately
        transport http {
            read_timeout 1h
            write_timeout 1h
        }
    }
}
```

Caddy obtains and renews the certificate automatically and sends `X-Forwarded-*`
headers by default. To put single sign-on in front, add `forward_auth` to an
oauth2-proxy / Authelia instance and set `S3FB_AUTH_DISABLED=true`.

### nginx

```nginx
server {
    listen 443 ssl http2;
    server_name s3.example.com;

    ssl_certificate     /etc/letsencrypt/live/s3.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/s3.example.com/privkey.pem;

    client_max_body_size 0;          # uploads of any size
    proxy_request_buffering off;     # stream uploads to the app
    proxy_buffering off;             # stream downloads and ZIPs
    proxy_read_timeout 3600s;
    proxy_send_timeout 3600s;

    location / {
        proxy_pass http://127.0.0.1:5001;
        proxy_set_header Host              $host;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host  $host;
    }
}

server {
    listen 80;
    server_name s3.example.com;
    return 301 https://$host$request_uri;
}
```

### Host name, not a sub-path

Serve the app at the root of its own host name (`https://s3.example.com/`). Sub-paths
such as `https://tools.example.com/s3/` are not supported, because the front end uses
absolute URLs (`/browse/...`, `/static/...`).

## 4. Environment variables

The full list is in the [README](../README.md#configuration) and in
[`.env.example`](../.env.example). The ones that matter most in production:

| Variable | Recommended | Why |
|---|---|---|
| `SECRET_KEY` | long random string | stable sessions across restarts and workers |
| `S3FB_AUTH_PASSWORD_HASH` | set | login required for every page and API call |
| `S3FB_ALLOWED_ENDPOINTS` | your endpoints | stops the server from connecting to arbitrary URLs (SSRF) |
| `SESSION_COOKIE_SECURE` | `true` | cookie only sent over HTTPS |
| `S3FB_TRUST_PROXY` | `true` behind a proxy | correct scheme, host and client address |
| `S3FB_VERIFY_TLS` / `S3FB_CA_BUNDLE` | `true` / your CA | keep certificate verification on; mount a CA bundle for private CAs |
| `S3FB_DOWNLOAD_MODE` | `presigned` | `proxy` if browsers cannot reach the S3 endpoint |

To use a private CA bundle, mount it read-only and point to it:

```yaml
# docker-compose.override.yml
services:
  s3fastbrowser:
    volumes:
      - /etc/ssl/certs/my-ca.pem:/certs/ca.pem:ro
    environment:
      S3FB_CA_BUNDLE: /certs/ca.pem
```

## 5. Presigned downloads and CORS

In `presigned` mode the browser downloads objects directly from the S3 endpoint using
a short-lived signed URL. This is the fastest option and needs no CORS configuration
(it is a normal navigation, not an XHR). It does require that users can resolve and
reach the endpoint URL that the app uses. If the app talks to an internal address
(for example `http://minio:9000`), switch to `S3FB_DOWNLOAD_MODE=proxy`.

The *Save to folder* option streams through the app (`/proxy-download`) in both modes.

## 6. Upgrades

```bash
cd /opt/s3fastbrowser
git fetch --tags
git checkout v1.8.0            # or: git pull
docker compose up -d --build
docker compose logs --tail=50 s3fastbrowser
```

Read [CHANGELOG.md](../CHANGELOG.md) before upgrading. Sessions are kept on the
`s3fb-data` volume, so users stay signed in unless `SECRET_KEY` changes. To roll
back, check out the previous tag and rebuild. You can tag images per release with
`S3FB_IMAGE_TAG=1.8.0`.

## 7. Backups

S3FastBrowser stores no data of its own besides sessions:

- **`.env`**: your configuration and secrets. Back it up like any secret.
- **`s3fb-data` volume**: server-side sessions, including the S3 credentials users
  entered and their recent connections. It does not need a backup (losing it only
  signs everybody out), but if you do back it up, protect it as sensitive data.
  To wipe all sessions: `docker compose down && docker volume rm <project>_s3fb-data`.

The objects themselves live in your S3 storage; back them up with your storage's own
tools (replication, versioning, object lock).

## 8. Hardening checklist

- [ ] Port bound to `127.0.0.1` (or a private Docker network), TLS at the proxy
- [ ] Login enabled, or an SSO proxy in front with `S3FB_AUTH_DISABLED=true`
- [ ] `S3FB_ALLOWED_ENDPOINTS` lists only your storage endpoints
- [ ] `SECRET_KEY` set, `SESSION_COOKIE_SECURE=true`
- [ ] TLS verification on (`S3FB_VERIFY_TLS=true`)
- [ ] Users connect with least-privilege keys (read-only where possible)
- [ ] Logs collected (`docker compose logs`), image rebuilt regularly for updates

## 9. Demo stack on a server

The demo services are only started when the `demo` profile is selected. They can run
next to a production instance on another port:

```bash
S3FB_DEMO_PORT=5002 docker compose --profile demo up -d --build s3fastbrowser-demo

# remove only the demo (keeps the production container and its volume)
docker compose --profile demo rm -sf minio demo-seed s3fastbrowser-demo
docker volume rm "$(basename "$PWD")_minio-demo-data"
```

Do not use `docker compose --profile demo down -v` on a production checkout: it also
stops the main service and deletes its `s3fb-data` volume.

The demo MinIO image is configurable with `S3FB_DEMO_MINIO_IMAGE` (any S3-compatible
server that accepts `server /data` and the `MINIO_ROOT_*` variables works).
