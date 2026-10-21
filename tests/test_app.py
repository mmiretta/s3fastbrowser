from app import endpoint_allowed


def test_pages_and_health(make_client):
    client = make_client()
    assert client.get('/').status_code == 200
    assert client.get('/healthz').get_json()['status'] == 'ok'
    assert 'version' in client.get('/version').get_json()


def test_endpoint_allowlist_rules():
    allowed = ['https://s3.amazonaws.com', 'https://s3.*.amazonaws.com', 'http://minio:9000']
    assert endpoint_allowed('https://s3.amazonaws.com/', allowed)
    assert endpoint_allowed('https://s3.eu-west-1.amazonaws.com', allowed)
    assert endpoint_allowed('http://MINIO:9000', allowed)
    assert not endpoint_allowed('http://minio:9001', allowed)
    assert not endpoint_allowed('http://169.254.169.254', allowed)
    assert not endpoint_allowed('https://user:pw@s3.amazonaws.com', allowed)
    assert not endpoint_allowed('file:///etc/passwd', allowed)
    assert endpoint_allowed('http://anything:1234', [])


def test_connect_refuses_endpoint_outside_allowlist(make_client):
    client = make_client(allowed=['https://s3.example.com'])
    r = client.post('/connect', json={'access_key': 'a', 'secret_key': 'b', 'endpoint_url': 'http://internal-host.invalid:22'})
    assert r.status_code == 403


def test_connect_errors_are_generic(make_client):
    client = make_client()
    r = client.post('/connect', json={'access_key': 'a', 'secret_key': 'b', 'endpoint_url': 'http://127.0.0.1:9'})
    assert r.status_code == 400
    assert '127.0.0.1' not in r.get_json()['error']


def test_auth_required_when_password_set(make_client):
    client = make_client(password='s3cret')
    assert client.get('/').status_code == 302
    assert client.get('/buckets').status_code == 401
    assert client.get('/healthz').status_code == 200
    assert client.post('/login', data={'username': 'admin', 'password': 'nope'}).status_code == 401
    r = client.post('/login?next=//evil.example', data={'username': 'admin', 'password': 's3cret'})
    assert r.status_code == 302 and r.headers['Location'] == '/'
    assert client.get('/').status_code == 200


def test_browse_and_recent_connections_hide_secret(connected):
    data = connected.get('/browse/bucket-a?prefix=docs/').get_json()
    assert sorted(o['key'] for o in data['objects']) == ['docs/data.csv', 'docs/readme.txt']
    recent = connected.get('/recent-connections').get_json()['connections']
    assert recent and 'secret_key' not in recent[0] and 'access_key' not in recent[0]


def test_previews(connected):
    r = connected.get('/preview/bucket-a/_?key=docs/data.csv').get_json()
    assert r['type'] == 'csv' and '<script>' not in r['html'] and '&lt;script&gt;' in r['html']
    assert connected.get('/preview/bucket-a/_?key=docs/readme.txt').get_json()['type'] == 'text'
    assert connected.get('/preview/bucket-a/_?key=bin/blob').get_json()['type'] == 'fallback'
    assert connected.get('/preview/bucket-a/_?key=media/clip.mp4').get_json()['type'] == 'video'


def test_media_range_requests(connected):
    r = connected.get('/preview-media/bucket-a/_?key=media/clip.mp4', headers={'Range': 'bytes=10-19'})
    assert r.status_code == 206
    assert r.headers['Content-Range'] == 'bytes 10-19/1000'
    assert r.data == b'0123456789'


def test_zip_download(connected):
    import io
    import zipfile
    r = connected.post('/download-selected/bucket-a', json={'keys': ['docs/']})
    assert r.status_code == 200
    names = zipfile.ZipFile(io.BytesIO(r.data)).namelist()
    assert sorted(names) == ['docs/data.csv', 'docs/readme.txt']


def test_settings_validation(connected):
    assert connected.post('/api/settings', json={'chunk_size': 1}).status_code == 400
    assert connected.post('/api/settings', json={'chunk_size': 16}).status_code == 200
    assert connected.get('/api/settings').get_json()['chunk_size'] == 16
