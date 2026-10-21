# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[semantic versioning](https://semver.org/).

## [Unreleased]

## [1.8.0] - 2026-10-21

First public release.

### Added
- Optional built-in authentication (single admin account from `S3FB_AUTH_PASSWORD`
  or a password hash), with login rate limiting.
- Endpoint allowlist (`S3FB_ALLOWED_ENDPOINTS`) to prevent server-side request
  forgery through the connection form; generic connection errors.
- Demo mode: `docker compose --profile demo` starts a local MinIO, fills it with
  generated sample files and runs a pre-configured instance.
- Audio preview plugin; video and audio stream with HTTP Range support (seeking).
- External preview plugins (`S3FB_PLUGIN_DIRS`), plugin priorities and partial reads
  (`PRIORITY`, `MAX_BYTES`).
- Optional bucket in the connection form for bucket-scoped credentials.
- Proxy download mode (`S3FB_DOWNLOAD_MODE=proxy`) for endpoints browsers cannot reach.
- Folder ZIP download button, object tags in the details dialog, `/healthz` endpoint.
- Security headers (Content-Security-Policy, X-Frame-Options, ...).
- Test suite (pytest + moto), deployment, plugin and preview documentation.

### Changed
- ZIP downloads are streamed with constant memory instead of being built in RAM.
- TLS certificates of S3 endpoints are verified by default (`S3FB_VERIFY_TLS`,
  `S3FB_CA_BUNDLE`).
- Recent connections never send the secret key to the browser; reconnecting happens
  on the server.
- Multipart threshold and chunk size settings are now applied to uploads.
- The container runs as a non-root user with gunicorn `gthread` workers and binds to
  `127.0.0.1` in the compose file.
- Dependencies updated (Flask 3, Werkzeug 3, Flask-Session 0.8, gunicorn 23).
- Text, JSON, CSV and HTML previews now take precedence correctly over the generic
  text plugin; text files are shown with line numbers.

### Fixed
- Object names, metadata and other values from S3 are escaped everywhere in the UI.
- CSV and Excel previews escape cell contents.
- "Download Selected" creates a single ZIP (a duplicate function overrode it).
- Pagination controls hidden when a folder fits in one page.
- Unreadable text in striped tables in the dark theme.

### Removed
- Unused OpenCV / NumPy dependencies (much smaller image).
