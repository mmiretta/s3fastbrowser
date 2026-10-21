"""Images (JPEG, PNG, GIF, WebP, BMP, SVG, AVIF, ICO), shown inline."""
import base64

from preview_plugins._util import esc

NAME = 'image'
MAX_BYTES = None       # needs the whole image

EXTENSIONS = {
    '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png', '.gif': 'image/gif',
    '.webp': 'image/webp', '.bmp': 'image/bmp', '.svg': 'image/svg+xml',
    '.avif': 'image/avif', '.ico': 'image/x-icon',
}


def _mime(filename, content_type):
    if content_type and content_type.startswith('image/'):
        return content_type.split(';')[0].strip()
    for ext, mime in EXTENSIONS.items():
        if filename.lower().endswith(ext):
            return mime
    return 'application/octet-stream'


def can_preview(filename, content_type):
    return filename.lower().endswith(tuple(EXTENSIONS)) or bool(content_type and content_type.startswith('image/'))


def render_preview(fileobj, filename, content_type, bucket_name=None):
    data = fileobj.read()
    b64 = base64.b64encode(data).decode('ascii')
    # An <img> never runs scripts, even for SVG, so a data: URI is safe here.
    return (f'<div class="pv-image"><img src="data:{esc(_mime(filename, content_type))};base64,{b64}" '
            f'alt="{esc(filename)}"></div>')
