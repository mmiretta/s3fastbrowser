# Third-party notices

S3FastBrowser bundles the following assets in `static/vendor/` so that it works
without internet access. They keep their own licenses.

| Component | Version | License | Location |
|---|---|---|---|
| [Bootstrap](https://getbootstrap.com/) | 5.1.3 | MIT | `static/vendor/bootstrap/` |
| [Font Awesome Free](https://fontawesome.com/) | 6.0.0 | Icons: CC BY 4.0, fonts: SIL OFL 1.1, code: MIT | `static/vendor/fontawesome/` |
| [DM Sans](https://fonts.google.com/specimen/DM+Sans) | - | SIL Open Font License 1.1 | `static/vendor/fonts/` |
| [Space Grotesk](https://fonts.google.com/specimen/Space+Grotesk) | - | SIL Open Font License 1.1 | `static/vendor/fonts/` |

Python dependencies are installed from PyPI at build time (see `requirements.txt`) and
are not redistributed in this repository.

The demo profile uses a community build of [MinIO](https://github.com/minio/minio)
(GNU AGPL v3) as a separate container image; it is not part of S3FastBrowser.
