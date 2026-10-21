"""Runtime configuration, read from environment variables (and an optional .env)."""
import os
import secrets

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None or value == '':
        return default
    return value.strip().lower() in ('1', 'true', 'yes', 'on')


def _list(name: str) -> list:
    raw = os.environ.get(name, '')
    return [item.strip() for item in raw.split(',') if item.strip()]


class Config:
    """Application configuration."""

    # Flask. If SECRET_KEY is not set a random one is generated at start-up,
    # which means sessions do not survive a restart and do not work across
    # several gunicorn workers. Always set it in production.
    SECRET_KEY = os.environ.get('SECRET_KEY') or secrets.token_hex(32)
    SECRET_KEY_IS_RANDOM = not os.environ.get('SECRET_KEY')
    DEBUG = _bool('FLASK_DEBUG', False)

    # Server (used by run.py; the Docker image runs gunicorn)
    HOST = os.environ.get('HOST', '127.0.0.1')
    PORT = int(os.environ.get('PORT', 5001))

    # Data directory: server-side sessions and logs live here.
    DATA_DIR = os.environ.get('S3FB_DATA_DIR', os.path.join(os.getcwd(), 'data'))
    SESSION_DIR = os.environ.get('S3FB_SESSION_DIR', os.path.join(DATA_DIR, 'sessions'))

    # Logging. LOG_FILE is optional; logs always go to stdout.
    LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO').upper()
    LOG_FILE = os.environ.get('LOG_FILE', '')

    # Session
    PERMANENT_SESSION_LIFETIME = int(os.environ.get('PERMANENT_SESSION_LIFETIME', 8 * 3600))
    SESSION_COOKIE_SECURE = _bool('SESSION_COOKIE_SECURE', False)

    # Built-in authentication (single admin account). Enabled when a password
    # or a password hash is configured.
    AUTH_USERNAME = os.environ.get('S3FB_AUTH_USERNAME', 'admin')
    AUTH_PASSWORD = os.environ.get('S3FB_AUTH_PASSWORD', '')
    AUTH_PASSWORD_HASH = os.environ.get('S3FB_AUTH_PASSWORD_HASH', '')
    # Set to true only when another layer (SSO proxy, VPN...) already
    # authenticates every request. Silences the "no authentication" warning.
    AUTH_DISABLED = _bool('S3FB_AUTH_DISABLED', False)

    # Endpoint allowlist (SSRF protection). Comma-separated list of allowed
    # endpoint URLs or shell-style patterns, e.g.
    #   https://s3.amazonaws.com,https://s3.*.amazonaws.com,http://minio:9000
    # Empty means "any endpoint".
    ALLOWED_ENDPOINTS = _list('S3FB_ALLOWED_ENDPOINTS')

    # Pre-filled values for the connection form.
    DEFAULT_ENDPOINT = os.environ.get('S3FB_DEFAULT_ENDPOINT', 'https://s3.amazonaws.com')
    DEFAULT_REGION = os.environ.get('S3FB_DEFAULT_REGION', 'us-east-1')

    # Optional CORS (not needed for normal use: the UI is served by the app).
    CORS_ORIGINS = _list('S3FB_CORS_ORIGINS')

    # Downloads: "presigned" sends the browser to a presigned URL on the S3
    # endpoint; "proxy" streams the object through this app (use it when the
    # browser cannot reach the S3 endpoint directly).
    DOWNLOAD_MODE = os.environ.get('S3FB_DOWNLOAD_MODE', 'presigned').strip().lower()

    # Largest object a preview plugin may read completely (MB).
    PREVIEW_MAX_MB = int(os.environ.get('S3FB_PREVIEW_MAX_MB', 25))

    # Demo mode: pre-fills the connection form with the bundled MinIO.
    DEMO = _bool('S3FB_DEMO', False)
    DEMO_ENDPOINT = os.environ.get('S3FB_DEMO_ENDPOINT', 'http://minio:9000')
    DEMO_ACCESS_KEY = os.environ.get('S3FB_DEMO_ACCESS_KEY', '')
    DEMO_SECRET_KEY = os.environ.get('S3FB_DEMO_SECRET_KEY', '')


class S3Config:
    """S3-specific configuration."""

    # Verify TLS certificates of the S3 endpoint. Set to false only for
    # endpoints with self-signed certificates on a trusted network.
    VERIFY_TLS = _bool('S3FB_VERIFY_TLS', True)
    # Optional CA bundle for private certificate authorities.
    CA_BUNDLE = os.environ.get('S3FB_CA_BUNDLE', '')

    # Listing cache
    CACHE_TTL = 300

    # Pagination
    ITEMS_PER_PAGE = 20
    MAX_ITEMS_PER_PAGE = 1000

    # Extra object information
    INCLUDE_VERSIONS = True
    INCLUDE_TAGS = True

    # 'path' or 'auto' (virtual-hosted). Set per connection.
    S3_ADDRESSING_STYLE = 'path'

    @classmethod
    def verify(cls):
        """Value for boto3's `verify` argument."""
        if not cls.VERIFY_TLS:
            return False
        return cls.CA_BUNDLE or True


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False


class TestingConfig(Config):
    TESTING = True


config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
    'default': ProductionConfig,
}
