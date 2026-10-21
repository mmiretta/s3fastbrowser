# Contributing

Thanks for your interest in S3FastBrowser! Bug reports, ideas and pull requests are
welcome.

## Reporting bugs

Open an issue with:

- what you did, what you expected and what happened;
- the S3 implementation you connect to (AWS, MinIO, Ceph, ...), and the addressing
  style and region you use;
- the relevant lines of `docker compose logs s3fastbrowser` (remove access keys,
  bucket names or host names you do not want to publish).

Security problems: please do **not** open a public issue, see [SECURITY.md](SECURITY.md).

## Development setup

```bash
git clone https://github.com/mmiretta/s3fastbrowser.git
cd s3fastbrowser
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest
FLASK_DEBUG=true python run.py      # http://127.0.0.1:5001
```

The tests use [moto](https://github.com/getmoto/moto) to fake S3, so no storage is
needed. For manual testing, the demo stack gives you a MinIO with sample data:

```bash
docker compose --profile demo up -d --build s3fastbrowser-demo   # http://127.0.0.1:5002, demo / demo
```

## Project layout

```
src/app.py               Flask routes
src/auth.py              optional built-in login
src/config.py            environment variables
src/services/            boto3 wrapper (listing, search, versions, ZIP streaming)
src/preview_plugins/     one module per preview type (docs/PLUGINS.md)
templates/               HTML + inline JavaScript (no build step)
static/vendor/           Bootstrap, Font Awesome, fonts (bundled, no CDN)
demo/                    demo data generator
scripts/screenshots.py   documentation screenshots (Playwright)
tests/                   pytest suite
```

## Pull requests

- Keep changes focused; one feature or fix per pull request.
- Add or update tests for behaviour changes (`pytest` must pass).
- Escape anything that comes from S3 (keys, metadata, contents) before it reaches
  `innerHTML` (`esc()` in the templates, `preview_plugins._util.esc` in plugins).
- Do not add external CDN resources: the app must work offline and the
  Content-Security-Policy only allows `'self'`.
- Update the README / docs and add a line to [CHANGELOG.md](CHANGELOG.md) under
  *Unreleased*.
- If you change the UI, refresh the screenshots from the demo:
  `python scripts/screenshots.py --url http://127.0.0.1:5002 --out docs/screenshots`.
  Only ever take screenshots of demo data.

By contributing you agree that your contributions are licensed under the
[MIT License](LICENSE).
