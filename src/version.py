"""Version information for S3FastBrowser."""
import os

_VERSION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'VERSION')


def _read_version() -> str:
    try:
        with open(_VERSION_FILE, encoding='utf-8') as fh:
            return fh.read().strip()
    except OSError:
        return '0.0.0'


__version__ = _read_version()
__app_name__ = 'S3FastBrowser'
__author__ = 'Mariano Miretta'
__license__ = 'MIT'
__description__ = 'Fast, self-hosted web browser for S3-compatible object storage'
__url__ = 'https://github.com/mmiretta/s3fastbrowser'

FEATURES = [
    'Works with any S3-compatible endpoint (AWS S3, MinIO, Ceph RGW, ...)',
    'Fast browsing with native S3 pagination',
    'Object versions and delete markers (restore / permanent delete)',
    'Metadata, tags and presigned URLs',
    'Upload of files and whole folders, ZIP and folder downloads',
    'Pluggable in-browser previews based on file type',
    'Bucket policy and lifecycle editor',
    'Light / dark theme',
]


def get_version_info():
    """Complete version information (served at /version)."""
    return {
        'name': __app_name__,
        'version': __version__,
        'author': __author__,
        'license': __license__,
        'description': __description__,
        'url': __url__,
        'features': FEATURES,
    }
