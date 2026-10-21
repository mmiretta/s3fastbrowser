"""Optional built-in authentication: a single admin account from the environment.

Authentication is enabled when ``S3FB_AUTH_PASSWORD`` or
``S3FB_AUTH_PASSWORD_HASH`` is set. To create a hash, run::

    python src/auth.py hash-password

(or ``docker compose run --rm s3fastbrowser python src/auth.py hash-password``).

This is deliberately simple. For several users, single sign-on or MFA, put
S3FastBrowser behind an authenticating reverse proxy (oauth2-proxy, Authelia,
Authentik, ...) and set ``S3FB_AUTH_DISABLED=true``.
"""
import hmac
import logging
import sys
import threading
import time
from urllib.parse import urlparse

from flask import (Blueprint, current_app, jsonify, redirect, render_template,
                   request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

logger = logging.getLogger(__name__)

bp = Blueprint('auth', __name__)

# Endpoints that never require a login.
PUBLIC_ENDPOINTS = {'auth.login', 'auth.logout', 'static', 'healthz', 'favicon'}
# Paths that are full HTML pages: unauthenticated visits are redirected to the
# login form. Everything else is an API call and gets a JSON 401.
PAGE_ENDPOINTS = {'index', 'settings_page', 'bucket_settings_page'}

# Very small brute-force guard (per process): after MAX_FAILURES failed logins
# from one address within WINDOW seconds, further attempts are refused until
# the window has passed.
MAX_FAILURES = 5
WINDOW = 300
_failures = {}
_failures_lock = threading.Lock()


def auth_enabled(app=None) -> bool:
    cfg = (app or current_app).config
    return bool(cfg.get('AUTH_PASSWORD') or cfg.get('AUTH_PASSWORD_HASH'))


def _check_credentials(username: str, password: str) -> bool:
    cfg = current_app.config
    user_ok = hmac.compare_digest(username or '', cfg.get('AUTH_USERNAME', 'admin'))
    if cfg.get('AUTH_PASSWORD_HASH'):
        try:
            pass_ok = check_password_hash(cfg['AUTH_PASSWORD_HASH'], password or '')
        except (ValueError, TypeError):
            logger.error('S3FB_AUTH_PASSWORD_HASH is not a valid werkzeug password hash')
            pass_ok = False
    else:
        pass_ok = hmac.compare_digest((password or '').encode(), cfg['AUTH_PASSWORD'].encode())
    return user_ok and pass_ok


def _client_id() -> str:
    return request.remote_addr or 'unknown'


def _is_locked(client: str) -> bool:
    now = time.time()
    with _failures_lock:
        attempts = [t for t in _failures.get(client, []) if now - t < WINDOW]
        _failures[client] = attempts
        return len(attempts) >= MAX_FAILURES


def _record_failure(client: str):
    with _failures_lock:
        _failures.setdefault(client, []).append(time.time())


def _safe_next(target: str) -> str:
    """Only allow relative redirects after login (no open redirect)."""
    if not target:
        return '/'
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc or not target.startswith('/') or target.startswith('//'):
        return '/'
    return target


@bp.route('/login', methods=['GET', 'POST'])
def login():
    if not auth_enabled():
        return redirect('/')
    error = None
    if request.method == 'POST':
        client = _client_id()
        if _is_locked(client):
            logger.warning('Login refused for %s: too many failed attempts', client)
            error = 'Too many failed attempts. Try again in a few minutes.'
            return render_template('login.html', error=error), 429
        username = request.form.get('username', '')
        password = request.form.get('password', '')
        if _check_credentials(username, password):
            session.clear()
            session['auth_user'] = username
            session.permanent = True
            logger.info('User %s logged in from %s', username, client)
            return redirect(_safe_next(request.args.get('next', '/')))
        _record_failure(client)
        logger.warning('Failed login for user %r from %s', username, client)
        error = 'Invalid username or password.'
    return render_template('login.html', error=error), (401 if error else 200)


@bp.route('/logout', methods=['GET', 'POST'])
def logout():
    session.clear()
    return redirect(url_for('auth.login') if auth_enabled() else '/')


def init_auth(app):
    """Register the blueprint and the request guard."""
    app.register_blueprint(bp)

    if auth_enabled(app):
        logger.info('Built-in authentication enabled (user: %s)', app.config.get('AUTH_USERNAME'))
    elif app.config.get('AUTH_DISABLED'):
        logger.info('Built-in authentication disabled (S3FB_AUTH_DISABLED=true); '
                    'make sure another layer authenticates requests')
    else:
        logger.warning('*** No authentication configured. Anyone who can reach this app can '
                       'browse, upload and DELETE data with the credentials entered in it. '
                       'Set S3FB_AUTH_PASSWORD (or S3FB_AUTH_PASSWORD_HASH), or keep the app '
                       'bound to localhost. ***')

    @app.before_request
    def _require_login():
        if not auth_enabled():
            return None
        if request.endpoint in PUBLIC_ENDPOINTS or request.endpoint is None and request.path.startswith('/static/'):
            return None
        if session.get('auth_user'):
            return None
        if request.endpoint in PAGE_ENDPOINTS and request.method == 'GET':
            return redirect(url_for('auth.login', next=request.full_path.rstrip('?')))
        return jsonify({'error': 'Authentication required'}), 401

    @app.context_processor
    def _auth_context():
        return {
            'auth_enabled': auth_enabled(),
            'auth_user': session.get('auth_user'),
            'auth_warning': not auth_enabled() and not app.config.get('AUTH_DISABLED'),
        }


def _cli():
    if len(sys.argv) >= 2 and sys.argv[1] == 'hash-password':
        import getpass
        pw = getpass.getpass('Password: ')
        if pw != getpass.getpass('Repeat password: '):
            print('Passwords do not match', file=sys.stderr)
            sys.exit(1)
        print(generate_password_hash(pw))
        return
    print('usage: python src/auth.py hash-password', file=sys.stderr)
    sys.exit(2)


if __name__ == '__main__':
    _cli()
