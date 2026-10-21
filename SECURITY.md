# Security policy

## Reporting a vulnerability

Please report security problems privately, using GitHub's
[private vulnerability reporting](https://github.com/mmiretta/s3fastbrowser/security/advisories/new)
for this repository. Do not open a public issue.

Include the version (shown in the footer and at `/version`), how to reproduce the
problem and its impact. You should get an answer within a few days.

## Supported versions

Only the latest release receives security fixes.

## Deployment advice

S3FastBrowser handles live storage credentials and can delete data. Before exposing
it to anyone but yourself, read the *Security* section of the README and the
hardening checklist in [docs/DEPLOY.md](docs/DEPLOY.md#8-hardening-checklist):
enable authentication, set `S3FB_ALLOWED_ENDPOINTS`, and serve it over TLS.
