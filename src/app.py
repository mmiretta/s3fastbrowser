"""S3FastBrowser: a web browser for S3-compatible object storage."""
import fnmatch
import logging
import mimetypes
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlencode, urlparse

from flask import Flask, Response, jsonify, render_template, request, session
from flask_session import Session

from auth import init_auth
from config import Config, S3Config, config
from preview_plugins import get_plugins
from services.s3_service import S3Service
from version import __version__, get_version_info

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
_handlers = [logging.StreamHandler(sys.stdout)]
if Config.LOG_FILE:
    os.makedirs(os.path.dirname(os.path.abspath(Config.LOG_FILE)), exist_ok=True)
    _handlers.append(logging.FileHandler(Config.LOG_FILE))
logging.basicConfig(
    level=getattr(logging, Config.LOG_LEVEL, logging.INFO),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=_handlers,
)
# boto/urllib3 are very chatty at DEBUG/INFO.
for _noisy in ('botocore', 'boto3', 'urllib3', 's3transfer'):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

logger = logging.getLogger('s3fastbrowser')

# 7 days is the ceiling for a presigned URL: SigV4 refuses anything longer.
PRESIGNED_MAX_EXPIRES = 7 * 24 * 3600

DEFAULT_SETTINGS = {
    'show_delete_markers': False,
    'show_bucket_settings': True,
    'multipart_threshold': 8,   # MB
    'chunk_size': 8,            # MB
    'concurrent_uploads': 5,
}
ADDRESSING_STYLES = ('path', 'auto', 'virtual')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def endpoint_allowed(endpoint_url: str, allowed: list) -> bool:
    """Check an endpoint URL against the S3FB_ALLOWED_ENDPOINTS allowlist.

    Each entry is either a full URL (scheme://host[:port]) or a shell-style
    pattern (``https://*.example.com``). Comparison is done on the normalized
    scheme://host[:port] of the endpoint, so paths and trailing slashes do not
    matter. An empty allowlist allows everything.
    """
    if not allowed:
        return True
    parsed = urlparse(endpoint_url or '')
    if parsed.scheme not in ('http', 'https') or not parsed.hostname:
        return False
    if parsed.username or parsed.password:
        return False
    netloc = parsed.hostname.lower()
    if parsed.port:
        netloc += f':{parsed.port}'
    normalized = f'{parsed.scheme}://{netloc}'
    for entry in allowed:
        entry = entry.strip().rstrip('/').lower()
        if not entry:
            continue
        if fnmatch.fnmatchcase(normalized, entry):
            return True
    return False


def load_settings():
    settings = dict(DEFAULT_SETTINGS)
    settings.update(session.get('settings') or {})
    return settings


def clean_settings(data: dict) -> dict:
    """Validate settings posted by the UI; unknown keys are ignored."""
    out = load_settings()

    def _int(name, lo, hi):
        try:
            value = int(data.get(name, out[name]))
        except (TypeError, ValueError):
            raise ValueError(f'{name} must be a number')
        if not lo <= value <= hi:
            raise ValueError(f'{name} must be between {lo} and {hi}')
        out[name] = value

    for flag in ('show_delete_markers', 'show_bucket_settings'):
        if flag in data:
            out[flag] = bool(data[flag])
    _int('multipart_threshold', 5, 5000)
    _int('chunk_size', 5, 5000)
    _int('concurrent_uploads', 1, 10)
    return out


def build_s3_service(cfg: dict):
    settings = load_settings()
    return S3Service(
        access_key_id=cfg['access_key'],
        secret_access_key=cfg['secret_key'],
        endpoint_url=cfg['endpoint_url'],
        region=cfg.get('region') or 'us-east-1',
        multipart_threshold=settings['multipart_threshold'],
        chunk_size=settings['chunk_size'],
        addressing_style=cfg.get('addressing_style', 'path'),
    )


def get_s3_service():
    """S3 service for the current session, or None if not connected."""
    cfg = session.get('s3_config')
    if not cfg:
        return None
    try:
        return build_s3_service(cfg)
    except Exception as e:
        logger.error(f"Error creating S3 service from session: {e}")
        return None


def load_recent_connections():
    return session.get('recent_connections', [])


def save_recent_connection(data):
    """Remember a connection in the (server-side) session, newest first, max 10."""
    connections = [c for c in load_recent_connections()
                   if c.get('endpoint_url') != data.get('endpoint_url')
                   or c.get('access_key') != data.get('access_key')]
    entry = dict(data)
    entry['last_used'] = datetime.now(timezone.utc).isoformat()
    connections.insert(0, entry)
    session['recent_connections'] = connections[:10]
    session.permanent = True


def public_connection(conn: dict) -> dict:
    """A recent connection as sent to the browser: never includes the secret."""
    key = conn.get('access_key', '')
    masked = key[:4] + '…' + key[-3:] if len(key) > 8 else '…'
    return {
        'endpoint_url': conn.get('endpoint_url'),
        'region': conn.get('region'),
        'addressing_style': conn.get('addressing_style'),
        'bucket': conn.get('bucket'),
        'access_key_masked': masked,
        'last_used': conn.get('last_used'),
    }


def arg_key(key: str) -> str:
    """Object keys may come as '?key=' (keeps a leading '/', which a URL path loses)."""
    return request.args.get('key', key) if 'key' in request.args else key


def arg_prefix(prefix: str) -> str:
    return request.args.get('prefix', prefix) if 'prefix' in request.args else prefix


def content_disposition(kind: str, filename: str) -> str:
    """RFC 6266 Content-Disposition with an ASCII fallback and a UTF-8 name."""
    ascii_name = re.sub(r'[^A-Za-z0-9._-]', '_', filename) or 'download'
    return f"{kind}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


def no_connection():
    return jsonify({'error': 'No active S3 connection'}), 400


def _connect(data: dict):
    """Validate connection parameters, test them and store them in the session.

    Returns (response, status). Error messages are deliberately generic: the
    detailed reason goes to the server log only, so the endpoint cannot be used
    to probe which hosts/ports exist on the server's network.
    """
    access_key = (data.get('access_key') or '').strip()
    secret_key = data.get('secret_key') or ''
    endpoint_url = (data.get('endpoint_url') or Config.DEFAULT_ENDPOINT).strip().rstrip('/')
    region = (data.get('region') or Config.DEFAULT_REGION).strip()
    addressing_style = data.get('addressing_style') or 'path'
    bucket = (data.get('bucket') or '').strip() or None

    if not access_key or not secret_key:
        return {'error': 'Access key and secret key are required'}, 400
    if addressing_style not in ADDRESSING_STYLES:
        return {'error': 'Invalid addressing style'}, 400
    parsed = urlparse(endpoint_url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname:
        return {'error': 'The endpoint must be an http:// or https:// URL'}, 400
    if not endpoint_allowed(endpoint_url, Config.ALLOWED_ENDPOINTS):
        logger.warning(f"Connection to non-allowlisted endpoint refused: {endpoint_url}")
        return {'error': 'This endpoint is not in the list of allowed endpoints '
                         '(S3FB_ALLOWED_ENDPOINTS).'}, 403

    cfg = {
        'access_key': access_key,
        'secret_key': secret_key,
        'endpoint_url': endpoint_url,
        'region': region,
        'addressing_style': addressing_style,
        'bucket': bucket,
    }
    try:
        ok = build_s3_service(cfg).test_connection(bucket=bucket)
    except Exception as e:
        logger.warning(f"Connection test to {endpoint_url} raised: {e}")
        ok = False
    if not ok:
        return {'error': 'Could not connect to S3. Check the endpoint, credentials and '
                         'region (details are in the server log).'}, 400

    session['s3_config'] = cfg
    session.permanent = True
    save_recent_connection(cfg)
    logger.info(f"Connected to S3 endpoint {endpoint_url} ({addressing_style} addressing)")
    return {'message': 'Successfully connected to S3', 'bucket': bucket}, 200


def _stream_object(s3_service, bucket_name, key, forced_type=None):
    """Serve an object inline with HTTP Range support (for media players and PDFs)."""
    head = s3_service.get_object_head(bucket_name, key)
    size = int(head.get('ContentLength', 0))
    content_type = forced_type or head.get('ContentType') or ''
    if not content_type or content_type == 'application/octet-stream' or content_type == 'binary/octet-stream':
        content_type = mimetypes.guess_type(key)[0] or 'application/octet-stream'

    range_header = request.headers.get('Range')
    status, headers = 200, {
        'Accept-Ranges': 'bytes',
        'Content-Disposition': content_disposition('inline', os.path.basename(key) or 'object'),
        'Cache-Control': 'no-store',
        'X-Content-Type-Options': 'nosniff',
    }
    s3_range = None
    if range_header:
        m = re.match(r'bytes=(\d*)-(\d*)$', range_header.strip())
        if m and size > 0 and (m.group(1) or m.group(2)):
            if m.group(1):
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else size - 1
            else:  # suffix range: last N bytes
                start = max(size - int(m.group(2)), 0)
                end = size - 1
            end = min(end, size - 1)
            if start > end or start >= size:
                return Response(status=416, headers={'Content-Range': f'bytes */{size}'})
            s3_range = f'bytes={start}-{end}'
            status = 206
            headers['Content-Range'] = f'bytes {start}-{end}/{size}'
            headers['Content-Length'] = str(end - start + 1)
    if s3_range is None:
        headers['Content-Length'] = str(size)

    body = s3_service.get_object_partial(bucket_name, key, s3_range)['Body']

    def generate():
        try:
            for chunk in body.iter_chunks(chunk_size=1024 * 1024):
                yield chunk
        finally:
            body.close()

    return Response(generate(), status=status, mimetype=content_type, headers=headers)


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------
def create_app(config_name=None):
    config_name = config_name or os.environ.get('FLASK_CONFIG', 'production')
    base_dir = os.path.abspath(os.path.dirname(__file__))
    app = Flask(__name__,
                template_folder=os.path.join(base_dir, '..', 'templates'),
                static_folder=os.path.join(base_dir, '..', 'static'))
    app.config.from_object(config[config_name])

    if os.environ.get('S3FB_TRUST_PROXY', '').lower() in ('1', 'true', 'yes'):
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

    if Config.CORS_ORIGINS:
        from flask_cors import CORS
        CORS(app, origins=Config.CORS_ORIGINS, supports_credentials=True)

    # Server-side sessions: S3 credentials never leave the server, the browser
    # only holds an opaque, HttpOnly session id.
    os.makedirs(Config.SESSION_DIR, exist_ok=True)
    from cachelib.file import FileSystemCache
    app.config.update(
        SESSION_TYPE='cachelib',
        SESSION_CACHELIB=FileSystemCache(cache_dir=Config.SESSION_DIR, threshold=2000),
        SESSION_PERMANENT=True,
        SESSION_COOKIE_NAME='s3fb_session',
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax',
        SESSION_COOKIE_SECURE=Config.SESSION_COOKIE_SECURE,
        PERMANENT_SESSION_LIFETIME=timedelta(seconds=Config.PERMANENT_SESSION_LIFETIME),
        MAX_CONTENT_LENGTH=None,
    )
    Session(app)
    if Config.SECRET_KEY_IS_RANDOM:
        logger.warning('SECRET_KEY is not set: using a random key. Sessions will not survive '
                       'a restart and will not work with more than one worker.')
    if Config.ALLOWED_ENDPOINTS:
        logger.info(f"Endpoint allowlist active: {', '.join(Config.ALLOWED_ENDPOINTS)}")
    else:
        logger.info('No endpoint allowlist (S3FB_ALLOWED_ENDPOINTS): users may connect to any endpoint')
    if not S3Config.VERIFY_TLS:
        logger.warning('TLS certificate verification for S3 endpoints is DISABLED (S3FB_VERIFY_TLS=false)')

    init_auth(app)
    get_plugins()  # load (and log) preview plugins once at start-up

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault('X-Content-Type-Options', 'nosniff')
        resp.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
        resp.headers.setdefault('Referrer-Policy', 'same-origin')
        resp.headers.setdefault(
            'Content-Security-Policy',
            "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; media-src 'self' blob:; font-src 'self'; "
            "frame-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; "
            "form-action 'self'; frame-ancestors 'self'")
        return resp

    @app.context_processor
    def inject_globals():
        return {
            'app_version': __version__,
            'demo_mode': Config.DEMO,
            'default_endpoint': Config.DEMO_ENDPOINT if Config.DEMO else Config.DEFAULT_ENDPOINT,
            'default_region': Config.DEFAULT_REGION,
            'endpoint_allowlist': Config.ALLOWED_ENDPOINTS,
        }

    # ------------------------------------------------------------------ pages
    @app.route('/')
    def index():
        return render_template('index.html')

    @app.route('/settings')
    def settings_page():
        return render_template('settings.html', settings=load_settings())

    @app.route('/bucket/<bucket_name>/settings')
    def bucket_settings_page(bucket_name):
        return render_template('bucket_settings.html', bucket_name=bucket_name)

    @app.route('/favicon.ico')
    def favicon():
        return app.send_static_file('favicon.svg'), 200, {'Content-Type': 'image/svg+xml'}

    @app.route('/healthz')
    def healthz():
        return jsonify({'status': 'ok', 'version': __version__})

    @app.route('/version')
    def get_version():
        return jsonify(get_version_info())

    # --------------------------------------------------------------- settings
    @app.route('/api/settings', methods=['GET', 'POST'])
    def api_settings():
        if request.method == 'POST':
            try:
                session['settings'] = clean_settings(request.get_json(silent=True) or {})
            except ValueError as e:
                return jsonify({'error': str(e)}), 400
            session.permanent = True
            return jsonify({'message': 'Settings saved successfully'})
        return jsonify(load_settings())

    # ------------------------------------------------------------- connection
    @app.route('/connect', methods=['POST'])
    def connect_s3():
        body, status = _connect(request.get_json(silent=True) or {})
        return jsonify(body), status

    @app.route('/api/demo-connect', methods=['POST'])
    def demo_connect():
        """Demo mode only: connect to the bundled MinIO with the demo keys."""
        if not Config.DEMO or not Config.DEMO_ACCESS_KEY:
            return jsonify({'error': 'Demo mode is not enabled'}), 404
        body, status = _connect({
            'access_key': Config.DEMO_ACCESS_KEY,
            'secret_key': Config.DEMO_SECRET_KEY,
            'endpoint_url': Config.DEMO_ENDPOINT,
            'region': 'us-east-1',
            'addressing_style': 'path',
        })
        return jsonify(body), status

    @app.route('/api/connection-status')
    def connection_status():
        cfg = session.get('s3_config')
        if cfg:
            return jsonify({'connected': True, 'endpoint': cfg['endpoint_url'],
                            'region': cfg.get('region'), 'bucket': cfg.get('bucket')})
        return jsonify({'connected': False})

    @app.route('/disconnect', methods=['POST'])
    def disconnect():
        session.pop('s3_config', None)
        logger.info("S3 session disconnected")
        return jsonify({'message': 'Session disconnected successfully'})

    @app.route('/recent-connections', methods=['GET'])
    def get_recent_connections():
        return jsonify({'connections': [public_connection(c) for c in load_recent_connections()]})

    @app.route('/recent-connections/<int:index>/connect', methods=['POST'])
    def connect_recent(index):
        connections = load_recent_connections()
        if not 0 <= index < len(connections):
            return jsonify({'error': 'Invalid index'}), 400
        body, status = _connect(connections[index])
        return jsonify(body), status

    @app.route('/recent-connections/<int:index>', methods=['DELETE'])
    def delete_recent_connection(index):
        connections = load_recent_connections()
        if 0 <= index < len(connections):
            connections.pop(index)
            session['recent_connections'] = connections
            return jsonify({'message': 'Connection removed'})
        return jsonify({'error': 'Invalid index'}), 400

    # ---------------------------------------------------------------- buckets
    @app.route('/buckets')
    def list_buckets():
        s3_service = get_s3_service()
        if not s3_service:
            return no_connection()
        try:
            return jsonify({'buckets': [b.to_dict() for b in s3_service.list_buckets()]})
        except Exception as e:
            # Bucket-scoped credentials often cannot ListAllMyBuckets. If the
            # user gave a bucket when connecting, show just that one.
            scoped = (session.get('s3_config') or {}).get('bucket')
            if scoped:
                logger.info(f"list_buckets denied; showing the connection's bucket '{scoped}': {e}")
                return jsonify({'buckets': [{
                    'name': scoped, 'creation_date': None, 'region': None,
                    'versioning': None, 'encryption': {}, 'tags': {},
                    'objects_count': 0, 'total_size': 0,
                }]})
            logger.error(f"Error listing buckets: {e}")
            return jsonify({'error': f'Error listing buckets: {e}'}), 500

    @app.route('/bucket-info/<bucket_name>')
    def bucket_info(bucket_name):
        s3_service = get_s3_service()
        if not s3_service:
            return no_connection()
        try:
            return jsonify(s3_service.get_bucket_info(bucket_name))
        except Exception as e:
            logger.error(f"Error getting bucket info {bucket_name}: {e}")
            return jsonify({'error': f'Error getting bucket info: {e}'}), 500

    @app.route('/api/bucket/<bucket_name>/policy', methods=['GET', 'POST', 'DELETE'])
    def handle_bucket_policy(bucket_name):
        s3_service = get_s3_service()
        if not s3_service:
            return no_connection()
        try:
            if request.method == 'GET':
                return jsonify({'policy': s3_service.get_bucket_policy(bucket_name)})
            if request.method == 'POST':
                policy = (request.get_json(silent=True) or {}).get('policy')
                if not policy:
                    return jsonify({'error': 'Policy is required'}), 400
                s3_service.put_bucket_policy(bucket_name, policy)
                return jsonify({'message': 'Policy updated successfully'})
            s3_service.delete_bucket_policy(bucket_name)
            return jsonify({'message': 'Policy deleted successfully'})
        except Exception as e:
            logger.error(f"Bucket policy {request.method} failed for {bucket_name}: {e}")
            return jsonify({'error': str(e)}), 500

    @app.route('/api/bucket/<bucket_name>/lifecycle', methods=['GET', 'POST', 'DELETE'])
    def handle_bucket_lifecycle(bucket_name):
        s3_service = get_s3_service()
        if not s3_service:
            return no_connection()
        try:
            if request.method == 'GET':
                return jsonify({'lifecycle': s3_service.get_bucket_lifecycle(bucket_name)})
            if request.method == 'POST':
                rules = (request.get_json(silent=True) or {}).get('rules')
                if not rules:
                    return jsonify({'error': 'Rules are required'}), 400
                s3_service.put_bucket_lifecycle(bucket_name, rules)
                return jsonify({'message': 'Lifecycle updated successfully'})
            s3_service.delete_bucket_lifecycle(bucket_name)
            return jsonify({'message': 'Lifecycle deleted successfully'})
        except Exception as e:
            logger.error(f"Bucket lifecycle {request.method} failed for {bucket_name}: {e}")
            return jsonify({'error': str(e)}), 500

    # ---------------------------------------------------------------- listing
    @app.route('/browse/<bucket_name>', strict_slashes=False)
    @app.route('/browse/<bucket_name>/<path:prefix>', strict_slashes=False)
    def browse_bucket(bucket_name, prefix=''):
        """List one "folder" using native S3 pagination."""
        prefix = arg_prefix(prefix)
        s3_service = get_s3_service()
        if not s3_service:
            return no_connection()
        max_keys = min(max(request.args.get('per_page', S3Config.ITEMS_PER_PAGE, type=int), 1),
                       S3Config.MAX_ITEMS_PER_PAGE)
        continuation_token = request.args.get('continuation_token')
        try:
            result = s3_service.list_objects(bucket=bucket_name, prefix=prefix, max_keys=max_keys,
                                             continuation_token=continuation_token)
            return jsonify({
                'bucket': bucket_name,
                'prefix': prefix,
                'objects': [obj.to_dict() for obj in result['objects']],
                'common_prefixes': result['common_prefixes'],
                'next_token': result['next_token'],
                'is_truncated': result['is_truncated'],
                'total': result['total'],
            })
        except Exception as e:
            logger.error(f"Error browsing {bucket_name}/{prefix}: {e}")
            return jsonify({'error': f'Error browsing bucket: {e}'}), 500

    @app.route('/list-recursive/<bucket_name>/', defaults={'prefix': ''})
    @app.route('/list-recursive/<bucket_name>/<path:prefix>')
    def list_recursive(bucket_name, prefix):
        prefix = arg_prefix(prefix)
        s3_service = get_s3_service()
        if not s3_service:
            return no_connection()
        try:
            return jsonify({'objects': s3_service.list_objects_recursive(bucket_name, prefix)})
        except Exception as e:
            logger.error(f"Error listing {bucket_name}/{prefix} recursively: {e}")
            return jsonify({'error': str(e)}), 500

    @app.route('/search/<bucket_name>')
    def search_objects(bucket_name):
        """Search objects by name and/or extension under an optional prefix."""
        s3_service = get_s3_service()
        if not s3_service:
            return no_connection()
        search_term = request.args.get('q', '').strip()
        extensions = request.args.get('extensions', '').strip()
        prefix = request.args.get('prefix', '').strip()
        max_keys = min(request.args.get('max_keys', 1000, type=int), 10000)
        if not search_term and not extensions:
            return jsonify({'error': 'Please provide a search term or file extension filter'}), 400
        try:
            objects = [o.to_dict() for o in s3_service.search_objects(
                bucket=bucket_name, search_term=search_term, extensions=extensions,
                prefix=prefix, max_keys=max_keys)]
            return jsonify({'objects': objects, 'total': len(objects), 'search_term': search_term,
                            'extensions': extensions, 'prefix': prefix})
        except Exception as e:
            logger.error(f"Error searching in bucket {bucket_name}: {e}")
            return jsonify({'error': f'Error searching objects: {e}'}), 500

    @app.route('/delete-markers/<bucket_name>')
    def list_delete_markers(bucket_name):
        s3_service = get_s3_service()
        if not s3_service:
            return no_connection()
        prefix = request.args.get('prefix', '')
        try:
            return jsonify({'delete_markers': s3_service.list_delete_markers(bucket_name, prefix=prefix)})
        except Exception as e:
            logger.error(f"Error listing delete markers for {bucket_name}: {e}")
            return jsonify({'error': f'Error listing delete markers: {e}'}), 500

    @app.route('/clear-cache', methods=['POST'])
    def clear_cache():
        s3_service = get_s3_service()
        if not s3_service:
            return no_connection()
        s3_service.clear_cache()
        return jsonify({'message': 'Cache cleared successfully'})

    # ---------------------------------------------------------------- objects
    @app.route('/object/<bucket_name>/<path:key>')
    def get_object_info(bucket_name, key):
        key = arg_key(key)
        s3_service = get_s3_service()
        if not s3_service:
            return no_connection()
        try:
            s3_obj = s3_service.get_object_info(bucket_name, key)
            if not s3_obj:
                return jsonify({'error': 'Object not found'}), 404
            return jsonify(s3_obj.to_dict())
        except Exception as e:
            logger.error(f"Error getting object info {bucket_name}/{key}: {e}")
            return jsonify({'error': f'Error getting information: {e}'}), 500

    @app.route('/object/<bucket_name>/<path:key>/versions')
    def get_object_versions(bucket_name, key):
        key = arg_key(key)
        s3_service = get_s3_service()
        if not s3_service:
            return no_connection()
        return jsonify({'versions': s3_service.get_object_versions(bucket_name, key)})

    @app.route('/objects/<bucket_name>/metadata', methods=['POST'])
    def get_objects_metadata(bucket_name):
        s3_service = get_s3_service()
        if not s3_service:
            return no_connection()
        keys = (request.get_json(silent=True) or {}).get('keys', [])
        if not keys:
            return jsonify({'error': 'No keys provided'}), 400
        return jsonify({'metadata': s3_service.get_objects_metadata(bucket_name, keys[:1000])})

    @app.route('/upload/<bucket_name>', methods=['POST'])
    def upload_object(bucket_name):
        s3_service = get_s3_service()
        if not s3_service:
            return no_connection()
        if 'file' not in request.files:
            return jsonify({'error': 'No file provided'}), 400
        file = request.files['file']
        key = request.form.get('key') or file.filename
        if not file.filename or not key:
            return jsonify({'error': 'Invalid filename'}), 400
        content_type = file.mimetype if file.mimetype and file.mimetype != 'application/octet-stream' \
            else mimetypes.guess_type(key)[0]
        try:
            if s3_service.upload_object_stream(bucket_name, key, file.stream, content_type=content_type):
                return jsonify({'message': f'File {key} uploaded successfully'})
            return jsonify({'error': 'Error uploading file'}), 500
        except Exception as e:
            logger.error(f"Error uploading {bucket_name}/{key}: {e}")
            return jsonify({'error': f'Error uploading file: {e}'}), 500

    def _download_url(s3_service, bucket_name, key, version_id=None):
        if Config.DOWNLOAD_MODE == 'proxy':
            params = {'key': key}
            if version_id:
                params['version_id'] = version_id
            return f"/proxy-download/{quote(bucket_name, safe='')}/_?{urlencode(params)}"
        if version_id:
            return s3_service.generate_presigned_url_for_version(bucket_name, key, version_id, 'get_object', 3600)
        return s3_service.generate_presigned_url(bucket_name, key, 'get_object', 3600)

    @app.route('/download/<bucket_name>/<path:key>')
    def download_object(bucket_name, key):
        key = arg_key(key)
        s3_service = get_s3_service()
        if not s3_service:
            return no_connection()
        url = _download_url(s3_service, bucket_name, key)
        if url:
            return jsonify({'download_url': url})
        return jsonify({'error': 'Error generating download URL'}), 500

    @app.route('/download/<bucket_name>/<path:key>/version/<version_id>')
    def download_object_version(bucket_name, key, version_id):
        key = arg_key(key)
        s3_service = get_s3_service()
        if not s3_service:
            return no_connection()
        url = _download_url(s3_service, bucket_name, key, version_id)
        if url:
            return jsonify({'download_url': url})
        return jsonify({'error': 'Error generating download URL for version'}), 500

    @app.route('/proxy-download/<bucket_name>/<path:key>')
    def proxy_download(bucket_name, key):
        """Stream an object through the app (no direct access to the endpoint needed)."""
        key = arg_key(key)
        s3_service = get_s3_service()
        if not s3_service:
            return no_connection()
        try:
            stream, content_type, content_length = s3_service.get_object_stream(
                bucket_name, key, version_id=request.args.get('version_id') or None)
            return Response(
                stream.iter_chunks(chunk_size=1024 * 1024),
                mimetype=content_type or 'application/octet-stream',
                headers={
                    'Content-Disposition': content_disposition('attachment', key.split('/')[-1] or 'download'),
                    'Content-Length': str(content_length),
                },
            )
        except Exception as e:
            logger.error(f"Error proxying download for {bucket_name}/{key}: {e}")
            return jsonify({'error': f'Error downloading object: {e}'}), 500

    @app.route('/presigned-urls/<bucket_name>', methods=['POST'])
    def get_presigned_urls(bucket_name):
        """Download URLs for a list of keys (presigned, or proxy URLs in proxy mode)."""
        s3_service = get_s3_service()
        if not s3_service:
            return no_connection()
        keys = (request.get_json(silent=True) or {}).get('keys', [])
        if not keys:
            return jsonify({'error': 'No keys provided'}), 400
        urls = {}
        for key in keys[:1000]:
            url = _download_url(s3_service, bucket_name, key)
            if url:
                urls[key] = url
        return jsonify({'urls': urls})

    @app.route('/presigned-url/<bucket_name>', methods=['POST'])
    def get_presigned_url(bucket_name):
        """One presigned GET URL with a caller-chosen TTL.

        The link downloads the object without any credentials, so the expiry is
        the only thing limiting it.
        """
        s3_service = get_s3_service()
        if not s3_service:
            return no_connection()
        data = request.get_json(silent=True) or {}
        key = data.get('key') or ''
        if not key:
            return jsonify({'error': 'No key provided'}), 400
        version_id = data.get('version_id') or None
        try:
            expires_in = int(data.get('expires_in', 3600))
        except (TypeError, ValueError):
            return jsonify({'error': 'expires_in must be a number of seconds'}), 400
        if not 1 <= expires_in <= PRESIGNED_MAX_EXPIRES:
            return jsonify({'error': 'expires_in must be between 1 second and 7 days'}), 400
        if version_id:
            url = s3_service.generate_presigned_url_for_version(bucket_name, key, version_id, 'get_object', expires_in)
        else:
            url = s3_service.generate_presigned_url(bucket_name, key, 'get_object', expires_in)
        if not url:
            return jsonify({'error': 'Could not generate presigned URL'}), 500
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        return jsonify({'url': url, 'expires_in': expires_in, 'expires_at': expires_at.isoformat()})

    @app.route('/api/download-folder/<bucket_name>/', defaults={'prefix': ''})
    @app.route('/api/download-folder/<bucket_name>/<path:prefix>')
    def download_folder(bucket_name, prefix):
        """Download a folder as a streamed ZIP file."""
        prefix = arg_prefix(prefix)
        s3_service = get_s3_service()
        if not s3_service:
            return no_connection()
        max_size_gb = request.args.get('max_size_gb', 10, type=int)
        try:
            name = prefix.rstrip('/').split('/')[-1] or bucket_name
            return Response(
                s3_service.download_folder_as_zip(bucket_name, prefix, max_total_size_gb=max_size_gb),
                mimetype='application/zip',
                headers={'Content-Disposition': content_disposition('attachment', f'{name}.zip')},
            )
        except ValueError as e:
            return jsonify({'error': str(e)}), 413
        except Exception as e:
            logger.error(f"Error downloading folder {bucket_name}/{prefix}: {e}")
            return jsonify({'error': f'Error downloading folder: {e}'}), 500

    @app.route('/download-selected/<bucket_name>', methods=['POST'])
    def download_selected(bucket_name):
        """Download a selection of keys (files and folders) as one streamed ZIP."""
        s3_service = get_s3_service()
        if not s3_service:
            return no_connection()
        keys = (request.get_json(silent=True) or {}).get('keys', [])
        if not keys:
            return jsonify({'error': 'No keys provided'}), 400
        zip_name = keys[0].rstrip('/').split('/')[-1] if len(keys) == 1 else 'selection'
        try:
            return Response(
                s3_service.download_keys_as_zip(bucket_name, keys),
                mimetype='application/zip',
                headers={'Content-Disposition': content_disposition('attachment', f'{zip_name}.zip')},
            )
        except ValueError as e:
            return jsonify({'error': str(e)}), 413
        except Exception as e:
            logger.error(f"Error creating selected ZIP for {bucket_name}: {e}")
            return jsonify({'error': str(e)}), 500

    # -------------------------------------------------------------- deleting
    @app.route('/delete/<bucket_name>/<path:key>', methods=['DELETE'])
    def delete_object(bucket_name, key):
        key = arg_key(key)
        s3_service = get_s3_service()
        if not s3_service:
            return no_connection()
        if s3_service.delete_object(bucket_name, key):
            return jsonify({'message': f'Object {key} deleted successfully'})
        return jsonify({'error': 'Error deleting object'}), 500

    @app.route('/delete-prefix/<bucket_name>/', defaults={'prefix': ''}, methods=['DELETE'])
    @app.route('/delete-prefix/<bucket_name>/<path:prefix>', methods=['DELETE'])
    def delete_prefix(bucket_name, prefix):
        prefix = arg_prefix(prefix)
        s3_service = get_s3_service()
        if not s3_service:
            return no_connection()
        if not prefix:
            return jsonify({'error': 'Refusing to delete a whole bucket from here'}), 400
        logger.info(f"Deleting prefix {bucket_name}/{prefix}")
        result = s3_service.delete_prefix_recursive(bucket_name, prefix)
        if result['error_count'] > 0:
            return jsonify({
                'message': f"Deleted {result['deleted_count']} objects with {result['error_count']} errors",
                **result,
            }), 207
        return jsonify({'message': f"Successfully deleted {result['deleted_count']} objects",
                        'deleted_count': result['deleted_count']})

    @app.route('/bulk-delete/<bucket_name>', methods=['POST'])
    def bulk_delete(bucket_name):
        """Delete a list of keys (in batches of 1000, the S3 maximum)."""
        s3_service = get_s3_service()
        if not s3_service:
            return no_connection()
        keys = (request.get_json(silent=True) or {}).get('keys', [])
        if not keys:
            return jsonify({'error': 'No keys provided'}), 400
        deleted = errors = 0
        messages = []
        for i in range(0, len(keys), 1000):
            result = s3_service._delete_batch(bucket_name, [{'Key': k} for k in keys[i:i + 1000]])
            deleted += result['deleted']
            errors += result['errors']
            messages.extend(result['error_messages'])
        s3_service.clear_cache()
        return jsonify({'deleted_count': deleted, 'error_count': errors, 'errors': messages})

    @app.route('/delete-marker/<bucket_name>/<path:key>/<version_id>', methods=['DELETE'])
    def delete_delete_marker(bucket_name, key, version_id):
        """Remove a delete marker, which restores the previous version."""
        key = arg_key(key)
        s3_service = get_s3_service()
        if not s3_service:
            return no_connection()
        try:
            s3_service.client.delete_object(Bucket=bucket_name, Key=key, VersionId=version_id)
            logger.info(f"Delete marker removed for {bucket_name}/{key} version {version_id}")
            return jsonify({'message': 'Delete marker removed, object restored'})
        except Exception as e:
            logger.error(f"Error removing delete marker for {bucket_name}/{key}: {e}")
            return jsonify({'error': f'Error removing delete marker: {e}'}), 500

    @app.route('/permanently-delete/<bucket_name>/<path:key>', methods=['DELETE'])
    def permanently_delete_object(bucket_name, key):
        """Delete an object and ALL of its versions and delete markers."""
        key = arg_key(key)
        s3_service = get_s3_service()
        if not s3_service:
            return no_connection()
        try:
            result = s3_service.permanently_delete_all_versions(bucket_name, key)
            if result['error_count'] > 0:
                return jsonify({
                    'message': f"Deleted {result['deleted_count']} versions with {result['error_count']} errors",
                    **result,
                }), 207
            return jsonify({'message': f"Permanently deleted {result['deleted_count']} versions of '{key}'",
                            'deleted_count': result['deleted_count']})
        except Exception as e:
            logger.error(f"Error permanently deleting {bucket_name}/{key}: {e}")
            return jsonify({'error': f'Error permanently deleting object: {e}'}), 500

    # --------------------------------------------------------------- previews
    @app.route('/preview/<bucket_name>/<path:key>')
    def preview_object(bucket_name, key):
        """Find the first plugin that can preview the object and return its HTML."""
        key = arg_key(key)
        s3_service = get_s3_service()
        if not s3_service:
            return no_connection()
        try:
            head = s3_service.get_object_head(bucket_name, key)
        except Exception as e:
            logger.error(f"Error reading {bucket_name}/{key} for preview: {e}")
            return jsonify({'error': f'Error previewing object: {e}'}), 500
        content_type = head.get('ContentType', '') or ''
        size = int(head.get('ContentLength', 0))
        limit = Config.PREVIEW_MAX_MB * 1024 * 1024

        for plugin in get_plugins():
            try:
                if not plugin.can_preview(key, content_type):
                    continue
                max_bytes = getattr(plugin, 'MAX_BYTES', None)
                if max_bytes == 0:
                    fileobj = None
                elif max_bytes is None:
                    if size > limit:
                        logger.info(f"{key}: {size} bytes is above S3FB_PREVIEW_MAX_MB for plugin {plugin.NAME}")
                        continue
                    fileobj = s3_service.get_object_partial(bucket_name, key)['Body']
                elif size == 0:
                    import io
                    fileobj = io.BytesIO(b'')
                else:
                    fileobj = s3_service.get_object_range(bucket_name, key, (0, min(max_bytes + 1, size)))
                html = plugin.render_preview(fileobj, key, content_type, bucket_name)
                if html:
                    return jsonify({'html': html, 'type': plugin.NAME})
            except Exception as e:
                logger.error(f"Preview plugin {plugin.NAME} failed for {bucket_name}/{key}: {e}")
        return jsonify({'type': 'none',
                        'html': '<div class="alert alert-warning">No preview available for this file '
                                f'(it may be larger than {Config.PREVIEW_MAX_MB} MB).</div>'})

    @app.route('/preview-media/<bucket_name>/<path:key>')
    def preview_media(bucket_name, key):
        """Inline streaming with Range support, used by the video/audio players."""
        key = arg_key(key)
        s3_service = get_s3_service()
        if not s3_service:
            return no_connection()
        try:
            return _stream_object(s3_service, bucket_name, key)
        except Exception as e:
            logger.error(f"Error streaming {bucket_name}/{key}: {e}")
            return jsonify({'error': f'Error streaming object: {e}'}), 500

    @app.route('/preview-pdf/<bucket_name>/<path:key>')
    def preview_pdf(bucket_name, key):
        key = arg_key(key)
        s3_service = get_s3_service()
        if not s3_service:
            return no_connection()
        try:
            return _stream_object(s3_service, bucket_name, key, forced_type='application/pdf')
        except Exception as e:
            logger.error(f"Error serving PDF preview for {bucket_name}/{key}: {e}")
            return jsonify({'error': f'Error serving PDF preview: {e}'}), 500

    # ----------------------------------------------------------------- errors
    @app.errorhandler(404)
    def not_found(error):
        logger.debug(f"404 {request.method} {request.path}")
        return jsonify({'error': 'Resource not found'}), 404

    @app.errorhandler(500)
    def internal_error(error):
        logger.error(f"Internal server error: {error}")
        return jsonify({'error': 'Internal server error'}), 500

    return app
