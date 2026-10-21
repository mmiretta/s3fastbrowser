import os
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src'))

os.environ.setdefault('S3FB_DATA_DIR', tempfile.mkdtemp(prefix='s3fb-test-'))
os.environ.setdefault('SECRET_KEY', 'test-secret')
os.environ.setdefault('AWS_ACCESS_KEY_ID', 'testing')
os.environ.setdefault('AWS_SECRET_ACCESS_KEY', 'testing')

from moto import mock_aws  # noqa: E402

import config as config_module  # noqa: E402


@pytest.fixture
def aws():
    with mock_aws():
        import boto3
        s3 = boto3.client('s3', region_name='us-east-1')
        s3.create_bucket(Bucket='bucket-a')
        s3.put_object(Bucket='bucket-a', Key='docs/readme.txt', Body=b'hello\nworld\n', ContentType='text/plain')
        s3.put_object(Bucket='bucket-a', Key='docs/data.csv', Body=b'a,b\n1,<script>\n', ContentType='text/csv')
        s3.put_object(Bucket='bucket-a', Key='bin/blob', Body=bytes(range(256)))
        s3.put_object(Bucket='bucket-a', Key='media/clip.mp4', Body=b'0123456789' * 100, ContentType='video/mp4')
        yield s3


@pytest.fixture
def make_client(monkeypatch):
    def _make(password='', allowed=None):
        from app import create_app
        monkeypatch.setattr(config_module.Config, 'ALLOWED_ENDPOINTS', allowed or [])
        app = create_app('testing')
        app.config['AUTH_PASSWORD'] = password
        app.config['AUTH_PASSWORD_HASH'] = ''
        return app.test_client()
    return _make


@pytest.fixture
def connected(aws, make_client):
    client = make_client()
    r = client.post('/connect', json={'access_key': 'testing', 'secret_key': 'testing',
                                      'endpoint_url': 'https://s3.amazonaws.com', 'region': 'us-east-1',
                                      'addressing_style': 'path'})
    assert r.status_code == 200, r.get_json()
    return client
